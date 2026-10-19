package local.zfsbackup.mixin;
import net.minecraft.world.level.chunk.storage.*;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;
@Mixin(ChunkStorage.class)
public interface ChunkStorageAccessor {@Accessor("worker") IOWorker zfs$chunkWorker();}
