package local.zfsbackup;
import java.util.concurrent.*;
import java.util.function.Supplier;
public final class SaveBarriers {
 public static CompletionStage<Void> after(CompletionStage<Void> companion,Executor server,Supplier<CompletionStage<Void>> nativeBarriers){
  CompletableFuture<Void> result=new CompletableFuture<>();
  companion.whenComplete((ignored,error)->{
   if(error!=null){result.completeExceptionally(error);return;}
   try{server.execute(()->{
    try{nativeBarriers.get().whenComplete((done,failure)->{if(failure==null)result.complete(null);else result.completeExceptionally(failure);});}
    catch(Exception failure){result.completeExceptionally(failure);}
   });}catch(Exception failure){result.completeExceptionally(failure);}
  });
  return result;
 }
}
