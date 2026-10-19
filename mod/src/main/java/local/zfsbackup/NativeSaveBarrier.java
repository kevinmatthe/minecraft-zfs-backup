package local.zfsbackup;
import local.zfsbackup.mixin.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.chunk.storage.*;
import java.util.*;
import java.util.concurrent.*;
/** Main-thread reference capture and marker submission; never invokes flushWorker/EntityStorage.flush. */
final class NativeSaveBarrier {
 private final MinecraftServer server;
 private final WriteGate gate;
 NativeSaveBarrier(MinecraftServer server,WriteGate gate){this.server=server;this.gate=gate;bind();}
 void bind(){
  if(!(server instanceof NativeStorageAccess.ServerObserved observed)||!observed.zfs$autosaveObserved())throw new IllegalStateException("autosave hooks unavailable");
  for(ServerLevel level:server.getAllLevels()){
   IOWorker chunks=((ChunkStorageAccessor)level.getChunkSource().chunkMap).zfs$chunkWorker();observed(chunks);
   NativeStorageAccess.Section poi=(NativeStorageAccess.Section)level.getPoiManager();poi.zfs$setWriteGate(gate);
   observed(((SimpleRegionStorageAccessor)poi.zfs$sectionStorage()).zfs$regionWorker());
   NativeStorageAccess.Entities entities=(NativeStorageAccess.Entities)((ServerLevelAccessor)level).zfs$entityManager();entities.zfs$setWriteGate(gate);
   SimpleRegionStorage storage=((EntityStorageAccessor)entities.zfs$permanentStorage()).zfs$entityStorage();
   observed(((SimpleRegionStorageAccessor)storage).zfs$regionWorker());
  }
 }
 private static void observed(IOWorker worker){if(!(worker instanceof NativeStorageAccess.StoreObserved capability)||!capability.zfs$storeObserved())throw new IllegalStateException("native store observer unavailable");}
 CompletionStage<Void> synchronize(long baseline){
  List<CompletionStage<?>> markers=new ArrayList<>();
  for(ServerLevel level:server.getAllLevels()){
   markers.add(((ChunkStorageAccessor)level.getChunkSource().chunkMap).zfs$chunkWorker().synchronize(true));
   NativeStorageAccess.Section poi=(NativeStorageAccess.Section)level.getPoiManager();markers.add(poi.zfs$sectionStorage().synchronize(true));
   NativeStorageAccess.Entities entities=(NativeStorageAccess.Entities)((ServerLevelAccessor)level).zfs$entityManager();
   markers.add(((EntityStorageAccessor)entities.zfs$permanentStorage()).zfs$entityStorage().synchronize(true));
  }
  return AutosaveHooks.errors().barrier(baseline,markers);
 }
}
