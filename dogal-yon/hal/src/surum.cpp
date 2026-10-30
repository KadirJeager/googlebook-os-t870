/*
 * Marks a natural-orientation HAL library with the panel rotation it was built for
 * (yamalar/dogal_yon.py PANEL_ROTATION); araclar/denetim.py and the tests read it back.
 */
#define GBOS_STR2(x) #x
#define GBOS_STR(x) GBOS_STR2(x)

extern "C" __attribute__((used, retain)) const char gbos_dogal_yon_surum[] =
        "gbos-dogal-yon PANEL_ROTATION=" GBOS_STR(GBOS_PANEL_ROTATION);
