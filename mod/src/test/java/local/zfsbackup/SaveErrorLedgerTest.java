package local.zfsbackup;
import java.util.*;
import java.util.concurrent.*;
public final class SaveErrorLedgerTest {
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static void failed(CompletionStage<?> stage,String message){try{stage.toCompletableFuture().join();throw new AssertionError(message);}catch(CompletionException expected){}}
 public static void main(String[] args){
  SaveErrorLedger ledger=new SaveErrorLedger();long baseline=ledger.generation();CompletableFuture<Void> write=new CompletableFuture<>();ledger.observe(write);
  CompletionStage<Void> pending=ledger.barrier(baseline,List.of(CompletableFuture.completedFuture(null)));check(!pending.toCompletableFuture().isDone(),"barrier must include original store futures");write.complete(null);pending.toCompletableFuture().join();
  baseline=ledger.generation();CompletableFuture<Void> gone=CompletableFuture.failedFuture(new IllegalStateException("already dequeued"));ledger.observe(gone);failed(ledger.barrier(baseline,List.of()),"already failed/dequeued future cannot vanish from evidence");
  baseline=ledger.generation();CompletableFuture<Void> later=new CompletableFuture<>(),nativeMarker=new CompletableFuture<>();ledger.observe(later);CompletionStage<Void> gated=ledger.barrier(baseline,List.of(nativeMarker));nativeMarker.complete(null);check(!gated.toCompletableFuture().isDone(),"native marker cannot outrun store observer");later.completeExceptionally(new Exception("write"));failed(gated,"store failure rejects snapshot");
  ledger.barrier(ledger.generation(),List.of(CompletableFuture.completedFuture(null))).toCompletableFuture().join();
  failed(ledger.barrier(ledger.generation(),List.of(CompletableFuture.failedFuture(new Exception("fsync")))),"native synchronize failure propagates");
  baseline=ledger.generation();ledger.failure();failed(ledger.barrier(baseline,List.of()),"reportChunkSaveFailure evidence persists");
  System.out.println("6 native store-error evidence checks passed");
 }
}
