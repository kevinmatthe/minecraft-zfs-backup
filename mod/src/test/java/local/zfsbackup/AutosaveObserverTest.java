package local.zfsbackup;
import java.util.*;
import java.util.concurrent.atomic.*;
public final class AutosaveObserverTest {
 public static void main(String[] args)throws Exception{
  SaveErrorLedger errors=new SaveErrorLedger();List<Long> completed=new ArrayList<>();AtomicInteger external=new AtomicInteger();
  AutosaveObserver observer=new AutosaveObserver(errors,completed::add,external::incrementAndGet);
  observer.tickStarted();observer.beforeAutosave();observer.fullSaveStarted();check(completed.isEmpty(),"never trigger before save return");observer.fullSaveReturned(true);observer.afterAutosave();check(completed.size()==1,"successful same-tick autosave triggers once");observer.afterAutosave();check(completed.size()==1,"one trigger only");
  observer.tickStarted();observer.beforeAutosave();observer.fullSaveStarted();observer.fullSaveReturned(false);observer.afterAutosave();check(completed.size()==1,"false save return cannot trigger");
  observer.tickStarted();observer.beforeAutosave();observer.fullSaveStarted();observer.tickStarted();observer.fullSaveReturned(true);observer.afterAutosave();check(completed.size()==1,"exception/missing return cannot arm future unrelated save");
  observer.tickStarted();observer.beforeAutosave();observer.fullSaveStarted();errors.failure();observer.fullSaveReturned(true);observer.afterAutosave();check(completed.size()==1,"swallowed failure since autosave began must reject");
  int before=external.get();observer.ownCapture(()->{observer.fullSaveStarted();observer.externalSaveStarted();observer.fullSaveReturned(true);});check(external.get()==before,"own forced flush must not cancel own checkpoint");
  try{observer.ownCapture(()->{throw new IllegalStateException("save threw");});}catch(IllegalStateException expected){}
  observer.externalSaveStarted();check(external.get()==before+1,"capture depth restored after exceptions; external save invokes cancellation");
  observer.tickStarted();observer.beforeAutosave();observer.fullSaveStarted();observer.fullSaveStarted();observer.fullSaveReturned(true);observer.fullSaveReturned(false);observer.afterAutosave();check(completed.size()==1,"nested successful save cannot hide failed outer autosave");
  System.out.println("7 autosave observer checks passed");
 }
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
}
