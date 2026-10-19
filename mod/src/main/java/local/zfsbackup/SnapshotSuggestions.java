package local.zfsbackup;

import com.mojang.brigadier.context.StringRange;
import com.mojang.brigadier.suggestion.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.*;

/** On-demand completion data only; no scheduled polling or game-state mutation. */
final class SnapshotSuggestions {
 private static final long CACHE_MILLIS=500;
 private final Supplier<CompletionStage<Map<String,Object>>> fetch;
 private final LongSupplier elapsed;
 private List<String> cached=List.of();
 private long expires;
 private boolean cacheValid;
 private CompletableFuture<List<String>> pending;
 SnapshotSuggestions(Supplier<CompletionStage<Map<String,Object>>> fetch,LongSupplier elapsed){this.fetch=fetch;this.elapsed=elapsed;}
 CompletableFuture<Suggestions> suggest(SuggestionsBuilder builder){
  try{
   return names().handle((names,error)->{
    try{
     String prefix=builder.getRemaining().toLowerCase(Locale.ROOT);
     if(error==null)for(String name:names)if(name.toLowerCase(Locale.ROOT).startsWith(prefix))builder.suggest(name);
     return builder.build();
    }catch(RuntimeException invalid){return new Suggestions(StringRange.at(builder.getStart()),List.of());}
   });
  }catch(RuntimeException failure){return Suggestions.empty();}
 }
 private CompletableFuture<List<String>> names(){
  CompletableFuture<List<String>> result;
  synchronized(this){
   if(cacheValid&&elapsed.getAsLong()<expires)return CompletableFuture.completedFuture(cached);
   if(pending!=null)return pending;
   result=new CompletableFuture<>();pending=result;
  }
  try{
   fetch.get().whenComplete((response,error)->{
    List<String> names;
    try{names=error==null?published(response):List.of();}catch(RuntimeException malformed){names=List.of();}
    finish(result,names);
   });
  }catch(RuntimeException submission){finish(result,List.of());}
  return result;
 }
 private void finish(CompletableFuture<List<String>> result,List<String> names){
  synchronized(this){cached=names;cacheValid=true;expires=elapsed.getAsLong()+CACHE_MILLIS;if(pending==result)pending=null;}
  result.complete(names);
 }
 private static List<String> published(Map<String,Object> response){
  if(response==null||!(response.get("snapshots") instanceof List<?> snapshots))return List.of();
  SortedSet<String> names=new TreeSet<>();
  for(Object item:snapshots){
   if(item instanceof Map<?,?> snapshot&&snapshot.get("name") instanceof String name&&SnapshotNames.isPublished(name))names.add(name);
  }
  return List.copyOf(names);
 }
}
