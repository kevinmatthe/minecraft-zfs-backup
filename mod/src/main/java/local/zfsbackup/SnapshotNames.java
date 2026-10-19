package local.zfsbackup;
import java.util.regex.Pattern;
/** Only the two managed published families of the configured local dataset. */
final class SnapshotNames {
 private static final Pattern PUBLISHED=Pattern.compile("mc-(?:live|hourly)-[0-9]{8}T[0-9]{6}Z");
 static boolean isPublished(String name){return name!=null&&PUBLISHED.matcher(name).matches();}
}
