package local.zfsbackup;
import java.util.*;
public final class WriteGateTest {
 public static void main(String[] args){
  WriteGate gate=new WriteGate();List<String> poiDirty=new ArrayList<>(List.of("poi")),unloads=new ArrayList<>(List.of("entity"));int[] loads={0},gameplay={0};
  gate.freeze();tick(gate,poiDirty,unloads,loads,gameplay);if(poiDirty.isEmpty()||unloads.isEmpty()||loads[0]!=1||gameplay[0]!=1)throw new AssertionError("freeze must preserve dirty/unload work while loads/gameplay continue");
  gate.restore();tick(gate,poiDirty,unloads,loads,gameplay);if(!poiDirty.isEmpty()||!unloads.isEmpty()||loads[0]!=2||gameplay[0]!=2)throw new AssertionError("restore resumes retained dirty/unload work");
  boolean[] originalFlags={false,true};SavingGuard<Integer> guard=new SavingGuard<>(index->originalFlags[index],(index,value)->originalFlags[index]=value);
  gate.freeze();guard.freeze(List.of(0,1));
  try{throw new IllegalStateException("prepare failed");}catch(IllegalStateException expected){try{guard.restore();}finally{gate.restore();}}
  if(originalFlags[0]||!originalFlags[1]||gate.suppressed())throw new AssertionError("failure restores exact prior noSave flags and extra write gates together");
  System.out.println("3 deferred tick-write gate checks passed");
 }
 static void tick(WriteGate gate,List<String> poiDirty,List<String> unloads,int[] loads,int[] gameplay){loads[0]++;gameplay[0]++;if(!gate.suppressed()){poiDirty.clear();unloads.clear();}}
}
