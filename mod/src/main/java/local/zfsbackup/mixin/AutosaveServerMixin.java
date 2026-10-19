package local.zfsbackup.mixin;
import local.zfsbackup.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.chunk.storage.RegionStorageInfo;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.*;
import java.util.function.BooleanSupplier;
/** Insert observers before AsyncPlayerSave's normal-priority Redirect replaces the original call. */
@Mixin(value=MinecraftServer.class,priority=1200)
public abstract class AutosaveServerMixin implements NativeStorageAccess.ServerObserved {
 @Inject(method="tickServer",at=@At("HEAD"),require=1)
 private void zfs$beginTick(BooleanSupplier time,CallbackInfo callback){AutosaveHooks.tickStarted((MinecraftServer)(Object)this);}
 @Inject(method="tickServer",at=@At(value="INVOKE",target="Lnet/minecraft/server/MinecraftServer;saveEverything(ZZZ)Z",shift=At.Shift.BEFORE),require=1)
 private void zfs$beforeAutosave(BooleanSupplier time,CallbackInfo callback){AutosaveHooks.beforeAutosave((MinecraftServer)(Object)this);}
 @Inject(method="tickServer",at=@At(value="INVOKE",target="Lnet/minecraft/server/MinecraftServer;saveEverything(ZZZ)Z",shift=At.Shift.AFTER),require=1)
 private void zfs$afterAutosave(BooleanSupplier time,CallbackInfo callback){AutosaveHooks.afterAutosave((MinecraftServer)(Object)this);}
 @Inject(method="saveEverything",at=@At("HEAD"),require=1)
 private void zfs$beforeSave(boolean silent,boolean flush,boolean force,CallbackInfoReturnable<Boolean> callback){AutosaveHooks.fullSaveStarted((MinecraftServer)(Object)this);}
 @Inject(method="saveEverything",at=@At("RETURN"),require=1)
 private void zfs$afterSave(boolean silent,boolean flush,boolean force,CallbackInfoReturnable<Boolean> callback){AutosaveHooks.fullSaveReturned((MinecraftServer)(Object)this,callback.getReturnValue());}
 @Inject(method="saveAllChunks",at=@At("HEAD"),require=1)
 private void zfs$beforeWorldSave(boolean silent,boolean flush,boolean force,CallbackInfoReturnable<Boolean> callback){AutosaveHooks.externalSaveStarted((MinecraftServer)(Object)this);}
 @Inject(method="reportChunkSaveFailure",at=@At("HEAD"),require=1)
 private void zfs$saveFailure(Throwable error,RegionStorageInfo storage,ChunkPos pos,CallbackInfo callback){AutosaveHooks.errors().failure();}
 @Override public boolean zfs$autosaveObserved(){return true;}
}
