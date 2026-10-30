/*
 * avc_kontrol: asks a compiled SELinux policy whether access vectors are allowed.
 *
 * Usage: avc_kontrol <binary policy>  < tuples
 *        avc_kontrol --cil <binary policy>   (writes the policy as CIL to stdout)
 * Each input line: "<scontext> <tcontext> <tclass> <perm>"; each output line repeats the
 * tuple prefixed with "ALLOW", "DENY-DONTAUDIT" (refused, and the policy says not to log it),
 * "DENY-TE" (no allow rule), "DENY-CONSTRAINT" (an MLS or other constraint, RBAC or typebounds
 * refuses it) or "UNKNOWN" (a context, class or permission the policy does not have).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sepol/sepol.h>
#include <sepol/kernel_to_cil.h>
#include <sepol/policydb/policydb.h>
#include <sepol/policydb/services.h>
#include <sepol/policydb/flask_types.h>

static int dump_cil(const char *path)
{
	FILE *fp = fopen(path, "re");
	sepol_policy_file_t *pf = NULL;
	sepol_policydb_t *p = NULL;
	if (!fp || sepol_policy_file_create(&pf) < 0 || sepol_policydb_create(&p) < 0) {
		fprintf(stderr, "cannot open policy %s\n", path);
		return 2;
	}
	sepol_policy_file_set_fp(pf, fp);
	if (sepol_policydb_read(p, pf) < 0) {
		fprintf(stderr, "cannot read policy %s\n", path);
		return 2;
	}
	int rc = sepol_kernel_policydb_to_cil(stdout, &p->p);
	sepol_policydb_free(p);
	sepol_policy_file_free(pf);
	fclose(fp);
	return rc == 0 ? 0 : 2;
}

int main(int argc, char **argv)
{
	if (argc == 3 && strcmp(argv[1], "--cil") == 0)
		return dump_cil(argv[2]);
	if (argc != 2) {
		fprintf(stderr, "usage: %s <binary policy> | --cil <binary policy>\n", argv[0]);
		return 2;
	}
	FILE *fp = fopen(argv[1], "re");
	if (!fp || sepol_set_policydb_from_file(fp) < 0) {
		fprintf(stderr, "cannot load policy %s\n", argv[1]);
		return 2;
	}
	fclose(fp);

	char line[4096];
	while (fgets(line, sizeof(line), stdin)) {
		char scon[1024], tcon[1024], cls[256], perm[256];
		if (sscanf(line, "%1023s %1023s %255s %255s", scon, tcon, cls, perm) != 4)
			continue;
		const char *verdict = "UNKNOWN";
		sepol_security_id_t ssid, tsid;
		sepol_security_class_t tclass;
		sepol_access_vector_t av;
		if (sepol_context_to_sid(scon, strlen(scon) + 1, &ssid) == 0 &&
		    sepol_context_to_sid(tcon, strlen(tcon) + 1, &tsid) == 0 &&
		    sepol_string_to_security_class(cls, &tclass) == 0 &&
		    sepol_string_to_av_perm(tclass, perm, &av) == 0) {
			struct sepol_av_decision avd;
			unsigned int reason = 0;
			if (sepol_compute_av_reason(ssid, tsid, tclass, av, &avd, &reason) == 0) {
				if ((avd.allowed & av) == av)
					verdict = "ALLOW";
				else if ((avd.auditdeny & av) != av)
					verdict = "DENY-DONTAUDIT";
				else if ((avd.allowed & av) != av && (reason & SEPOL_COMPUTEAV_TE))
					verdict = "DENY-TE";
				else
					verdict = "DENY-CONSTRAINT";
			}
		}
		printf("%s %s %s %s %s\n", verdict, scon, tcon, cls, perm);
	}
	return 0;
}
