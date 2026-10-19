package local.zfsbackup;

import java.io.IOException;
import java.util.function.BooleanSupplier;

/** Positive evidence from the actual vanilla stopServer save/close call sequence. */
public final class ShutdownEvidence {
 @FunctionalInterface public interface IoClose {void close()throws IOException;}
 private boolean began,saved,storageClosed,returned,failed;
 private int expectedDimensions,closedDimensions;
 public void begin(int dimensions){
  began=true;saved=false;storageClosed=false;returned=false;failed=false;
  expectedDimensions=dimensions;closedDimensions=0;
 }
 public boolean save(BooleanSupplier operation){
  try{boolean result=operation.getAsBoolean();saved=result;if(!result)failed=true;return result;}
  catch(RuntimeException|Error error){failed=true;throw error;}
 }
 public void closeDimension(IoClose operation)throws IOException{
  try{operation.close();closedDimensions++;}
  catch(IOException|RuntimeException|Error error){failed=true;throw error;}
 }
 public void closeStorage(IoClose operation)throws IOException{
  try{operation.close();storageClosed=true;}
  catch(IOException|RuntimeException|Error error){failed=true;throw error;}
 }
 public void returned(){returned=true;}
 public boolean clean(){return began&&saved&&storageClosed&&returned&&!failed&&expectedDimensions>0&&closedDimensions==expectedDimensions;}
}
