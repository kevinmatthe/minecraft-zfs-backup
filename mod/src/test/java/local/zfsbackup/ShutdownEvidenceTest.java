package local.zfsbackup;
import java.io.IOException;
public final class ShutdownEvidenceTest {
 static int passed;
 static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
 static ShutdownEvidence complete(boolean returned)throws IOException{
  ShutdownEvidence evidence=new ShutdownEvidence();evidence.begin(3);check(evidence.save(()->true),"original save return preserved");
  for(int i=0;i<3;i++)evidence.closeDimension(()->{});evidence.closeStorage(()->{});if(returned)evidence.returned();return evidence;
 }
 public static void main(String[] args)throws Exception{
  check(!new ShutdownEvidence().clean(),"missing optional instrumentation must fail closed");passed++;
  check(complete(true).clean(),"successful actual save/all-dimension close/storage close/return permits backup");passed++;
  check(!complete(false).clean(),"exceptional stop with no normal return must skip backup");passed++;
  ShutdownEvidence failedSave=new ShutdownEvidence();failedSave.begin(1);check(!failedSave.save(()->false),"false save return unchanged");failedSave.closeDimension(()->{});failedSave.closeStorage(()->{});failedSave.returned();check(!failedSave.clean(),"false save cannot authorize snapshot");passed++;
  ShutdownEvidence closedFailure=new ShutdownEvidence();closedFailure.begin(1);closedFailure.save(()->true);IOException io=new IOException("level close failed");
  try{closedFailure.closeDimension(()->{throw io;});throw new AssertionError("must preserve close exception");}catch(IOException expected){check(expected==io,"same level-close exception propagated");}
  // Vanilla catches that IOException, logs it, closes storage and returns normally.
  closedFailure.closeStorage(()->{});closedFailure.returned();check(!closedFailure.clean(),"caught level-close IOException cannot appear clean");passed++;
  ShutdownEvidence storageFailure=new ShutdownEvidence();storageFailure.begin(1);storageFailure.save(()->true);storageFailure.closeDimension(()->{});
  try{storageFailure.closeStorage(()->{throw io;});throw new AssertionError("must propagate storage exception");}catch(IOException expected){check(expected==io,"same storage-close exception propagated");}
  storageFailure.returned();check(!storageFailure.clean(),"caught storage-close IOException cannot appear clean");passed++;
  ShutdownEvidence missing=new ShutdownEvidence();missing.begin(2);missing.save(()->true);missing.closeDimension(()->{});missing.closeStorage(()->{});missing.returned();check(!missing.clean(),"missing dimension hook fails closed");passed++;
  ShutdownEvidence thrown=new ShutdownEvidence();thrown.begin(1);RuntimeException failure=new IllegalStateException("save threw");
  try{thrown.save(()->{throw failure;});throw new AssertionError("save throw must propagate");}catch(RuntimeException expected){check(expected==failure,"same save exception propagated");}
  thrown.closeDimension(()->{});thrown.closeStorage(()->{});thrown.returned();check(!thrown.clean(),"failed save never authorizes snapshot");passed++;
  ShutdownEvidence noSaveHook=new ShutdownEvidence();noSaveHook.begin(1);noSaveHook.closeDimension(()->{});noSaveHook.closeStorage(()->{});noSaveHook.returned();check(!noSaveHook.clean(),"missing save hook fails closed");passed++;
  System.out.println(passed+" clean-shutdown evidence checks passed");
 }
}
