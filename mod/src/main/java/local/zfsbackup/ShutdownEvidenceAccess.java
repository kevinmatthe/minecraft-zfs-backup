package local.zfsbackup;
/** Absent optional instrumentation never authorizes a shutdown backup. */
public interface ShutdownEvidenceAccess {
 boolean zfs$hasCleanShutdown();
}
