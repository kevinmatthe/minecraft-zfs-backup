package local.zfsbackup.mixin;

import local.zfsbackup.ShutdownEvidence;
import local.zfsbackup.ShutdownEvidenceAccess;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.storage.LevelStorageSource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import java.io.IOException;

/**
 * Observe the pinned vanilla call sequence without changing arguments, returns or exceptions.
 * Every hook is optional: a missing hook leaves incomplete evidence and disables shutdown backup.
 */
@Mixin(MinecraftServer.class)
public abstract class ShutdownEvidenceMixin implements ShutdownEvidenceAccess {
 @Unique private ShutdownEvidence zfs$shutdownEvidence;
 @Unique private ShutdownEvidence zfs$evidence(){
  if(zfs$shutdownEvidence==null)zfs$shutdownEvidence=new ShutdownEvidence();return zfs$shutdownEvidence;
 }
 @Inject(method="stopServer",at=@At("HEAD"),require=0)
 private void zfs$beginStop(CallbackInfo callback){
  int dimensions=0;for(ServerLevel level:((MinecraftServer)(Object)this).getAllLevels())if(level!=null)dimensions++;
  zfs$evidence().begin(dimensions);
 }
 @Redirect(method="stopServer",at=@At(value="INVOKE",target="Lnet/minecraft/server/MinecraftServer;saveAllChunks(ZZZ)Z"),require=0)
 private boolean zfs$observeSave(MinecraftServer server,boolean silent,boolean flush,boolean force){
  return zfs$evidence().save(()->server.saveAllChunks(silent,flush,force));
 }
 @Redirect(method="stopServer",at=@At(value="INVOKE",target="Lnet/minecraft/server/level/ServerLevel;close()V"),require=0)
 private void zfs$observeDimensionClose(ServerLevel level)throws IOException{
  zfs$evidence().closeDimension(level::close);
 }
 @Redirect(method="stopServer",at=@At(value="INVOKE",target="Lnet/minecraft/world/level/storage/LevelStorageSource$LevelStorageAccess;close()V"),require=0)
 private void zfs$observeStorageClose(LevelStorageSource.LevelStorageAccess storage)throws IOException{
  zfs$evidence().closeStorage(storage::close);
 }
 @Inject(method="stopServer",at=@At("RETURN"),require=0)
 private void zfs$normalStopReturn(CallbackInfo callback){zfs$evidence().returned();}
 @Override public boolean zfs$hasCleanShutdown(){return zfs$shutdownEvidence!=null&&zfs$shutdownEvidence.clean();}
}
