import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

/** Plays 2 s of a sine tone at track volume 0 through the speaker path, then reports how many
 *  frames the HAL consumed (AudioTrack.getPlaybackHeadPosition). No audible sound. */
public class SessizSes {
    public static void main(String[] args) throws Exception {
        int rate = 48000, seconds = 2;
        short[] pcm = new short[rate * seconds * 2];
        for (int i = 0; i < rate * seconds; i++) {
            short v = (short) (Math.sin(2 * Math.PI * 440 * i / rate) * 8000);
            pcm[2 * i] = v; pcm[2 * i + 1] = v;
        }
        AudioTrack t = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                .setAudioFormat(new AudioFormat.Builder().setSampleRate(rate)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).build())
                .setTransferMode(AudioTrack.MODE_STATIC)
                .setBufferSizeInBytes(pcm.length * 2).build();
        t.write(pcm, 0, pcm.length);
        t.setVolume(0f);
        t.play();
        Thread.sleep(seconds * 1000L + 500);
        int head = t.getPlaybackHeadPosition();
        System.out.println("state=" + t.getPlayState() + " headFrames=" + head + " expected=" + (rate * seconds));
        t.stop();
        t.release();
        System.exit(head >= rate * seconds * 9 / 10 ? 0 : 1);
    }
}
