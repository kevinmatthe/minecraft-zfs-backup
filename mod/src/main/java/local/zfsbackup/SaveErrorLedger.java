package local.zfsbackup;
import java.util.*;
import java.util.concurrent.*;
public final class SaveErrorLedger {
 private final java.util.concurrent.atomic.AtomicLong errors=new java.util.concurrent.atomic.AtomicLong();
 private final Set<CompletableFuture<Void>> pending=Collections.newSetFromMap(new IdentityHashMap<>());
 public long generation(){return errors.get();}
 public void failure(){errors.incrementAndGet();}
 /** Observe without replacing or recovering the original future used by Minecraft. */
 public void observe(CompletionStage<?> original){
  CompletableFuture<Void> witnessed=new CompletableFuture<>();synchronized(pending){pending.add(witnessed);}
  original.whenComplete((ignored,error)->{
   if(error!=null)failure();
   synchronized(pending){pending.remove(witnessed);}
   // Error generation is visible before the witness can release a checkpoint barrier.
   witnessed.complete(null);
  });
 }
 public CompletionStage<Void> barrier(long baseline,Collection<? extends CompletionStage<?>> nativeBarriers){
  List<CompletableFuture<?>> all=new ArrayList<>();for(CompletionStage<?> marker:nativeBarriers)all.add(marker.toCompletableFuture());
  synchronized(pending){all.addAll(pending);}
  return CompletableFuture.allOf(all.toArray(CompletableFuture[]::new)).thenRun(()->{
   if(errors.get()!=baseline)throw new CompletionException(new IllegalStateException("save I/O failure observed during checkpoint capture"));
  });
 }
}
