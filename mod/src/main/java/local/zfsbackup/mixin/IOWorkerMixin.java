package local.zfsbackup.mixin;
import local.zfsbackup.*;
import net.minecraft.world.level.chunk.storage.IOWorker;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.nbt.CompoundTag;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;
import java.util.concurrent.CompletableFuture;
/** Observe the original returned future; never cancel, replace, recover or drop a write. */
@Mixin(IOWorker.class)
public abstract class IOWorkerMixin implements NativeStorageAccess.StoreObserved {
 @Inject(method="store",at=@At("RETURN"),require=1)
 private void zfs$observeStore(ChunkPos pos,CompoundTag data,CallbackInfoReturnable<CompletableFuture<Void>> callback){AutosaveHooks.errors().observe(callback.getReturnValue());}
 @Override public boolean zfs$storeObserved(){return true;}
}
