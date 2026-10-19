package local.zfsbackup;
import java.util.*;
public final class SavingGuardTest {
 static final class Dimension {boolean noSave;Dimension(boolean flag){noSave=flag;}}
 public static void main(String[] args){
  Dimension overworld=new Dimension(false),nether=new Dimension(true),end=new Dimension(false);
  SavingGuard<Dimension> guard=new SavingGuard<>(d->d.noSave,(d,v)->d.noSave=v);
  guard.freeze(List.of(overworld,nether,end));
  if(!overworld.noSave||!nether.noSave||!end.noSave)throw new AssertionError("disable all dimensions");
  guard.restore();guard.restore();
  if(overworld.noSave||!nether.noSave||end.noSave)throw new AssertionError("exact prior flags and idempotent restore");
  SavingGuard<Dimension> partial=new SavingGuard<>(d->d.noSave,(d,v)->{if(d==end&&v)throw new IllegalStateException("failure");d.noSave=v;});
  try{partial.freeze(List.of(overworld,nether,end));throw new AssertionError("expected error");}catch(IllegalStateException expected){}
  partial.restore();if(overworld.noSave||!nether.noSave||end.noSave)throw new AssertionError("partial capture restores");
  System.out.println("3 dimension saving checks passed");
 }
}
