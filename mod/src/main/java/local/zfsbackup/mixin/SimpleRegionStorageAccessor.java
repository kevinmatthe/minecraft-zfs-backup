package local.zfsbackup.mixin;
import net.minecraft.world.level.chunk.storage.*;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;
@Mixin(SimpleRegionStorage.class)
public interface SimpleRegionStorageAccessor {@Accessor("worker") IOWorker zfs$regionWorker();}
