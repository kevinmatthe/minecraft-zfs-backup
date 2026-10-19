package local.zfsbackup;
public final class WriteGate {
 private boolean frozen;
 public void freeze(){frozen=true;}
 public void restore(){frozen=false;}
 public boolean suppressed(){return frozen;}
}
