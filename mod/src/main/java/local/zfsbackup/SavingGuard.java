package local.zfsbackup;
import java.util.*;
import java.util.function.*;
/** Capture each flag before changing it, including partially completed freezes. */
final class SavingGuard<T> {
 private final Function<T,Boolean> read;
 private final BiConsumer<T,Boolean> write;
 private final Map<T,Boolean> previous=new IdentityHashMap<>();
 SavingGuard(Function<T,Boolean> read,BiConsumer<T,Boolean> write){this.read=read;this.write=write;}
 void freeze(Iterable<T> dimensions){
  if(!previous.isEmpty())throw new IllegalStateException("saving guard already active");
  for(T dimension:dimensions){previous.put(dimension,read.apply(dimension));write.accept(dimension,true);}
 }
 void restore(){
  RuntimeException failure=null;
  for(var entry:previous.entrySet())try{write.accept(entry.getKey(),entry.getValue());}catch(RuntimeException e){if(failure==null)failure=e;else failure.addSuppressed(e);}
  if(failure!=null)throw failure;
  previous.clear();
 }
}
