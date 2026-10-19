package local.zfsbackup;
import java.util.function.LongConsumer;
public final class AutosaveObserver {
 @FunctionalInterface public interface Capture {void run()throws Exception;}
 private final SaveErrorLedger errors;
 private final LongConsumer completed;
 private final Runnable external;
 private boolean armed,returned,succeeded;
 private long baseline;
 private int fullDepth,captureDepth;
 public AutosaveObserver(SaveErrorLedger errors,LongConsumer completed,Runnable external){this.errors=errors;this.completed=completed;this.external=external;}
 public void tickStarted(){armed=false;returned=false;succeeded=false;fullDepth=0;}
 public void beforeAutosave(){armed=true;returned=false;succeeded=false;fullDepth=0;baseline=errors.generation();}
 public void fullSaveStarted(){externalSaveStarted();fullDepth++;}
 public void fullSaveReturned(boolean result){if(armed&&fullDepth==1){returned=true;succeeded=result;}fullDepth=Math.max(0,fullDepth-1);}
 public void afterAutosave(){boolean valid=armed&&returned&&succeeded&&errors.generation()==baseline;armed=false;if(valid)completed.accept(baseline);}
 public void externalSaveStarted(){if(captureDepth==0)external.run();}
 public void ownCapture(Capture capture)throws Exception{captureDepth++;try{capture.run();}finally{captureDepth--;}}
}
