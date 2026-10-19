package local.zfsbackup;

import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.*;
import java.nio.file.*;
import java.util.*;

/** Bounded, strictly typed JSON and same-directory atomic publication. */
public final class Mailbox {
 private static final int MAX_BYTES=16384;
 private final Path directory;
 public Mailbox(Path directory){this.directory=directory;}
 public Checkpoint.Heartbeat heartbeat()throws IOException{
  Map<String,Object> m=read("heartbeat.json");if(m==null)return null;
  protocol(m);return new Checkpoint.Heartbeat(number(m,"updated_ms"),bool(m,"ready"));
 }
 public Checkpoint.Response response()throws IOException{
  Map<String,Object> m=read("response.json");if(m==null)return null;protocol(m);
  String raw=string(m,"id"),phase=string(m,"phase");UUID id;
  try{id=UUID.fromString(raw);}catch(IllegalArgumentException e){throw new IOException("invalid response UUID",e);}
  if(!id.toString().equals(raw))throw new IOException("noncanonical UUID");
  if(!Set.of("SNAPSHOT_READY","COMMITTED","ERROR").contains(phase))throw new IOException("unknown response phase");
  String snapshot=phase.equals("ERROR")?string(m,"error"):string(m,"snapshot");
  return new Checkpoint.Response(id,phase,number(m,"updated_ms"),snapshot);
 }
 public void publish(Checkpoint.Request r)throws IOException{
  if(!Set.of("FROZEN","COMMIT","CANCEL").contains(r.phase()))throw new IOException("invalid request phase");
  String json="{\"protocol\":1,\"id\":\""+r.id()+"\",\"phase\":\""+r.phase()+"\",\"created_ms\":"+r.created()+",\"deadline_ms\":"+r.deadline();
  if(r.phase().equals("COMMIT"))json+=",\"committed_ms\":"+r.changed();
  if(r.phase().equals("CANCEL"))json+=",\"cancelled_ms\":"+r.changed();
  byte[] bytes=(json+"}\n").getBytes(StandardCharsets.UTF_8);
  Files.createDirectories(directory);
  Path temporary=Files.createTempFile(directory,"request-",".tmp");
  try{
   try(FileChannel file=FileChannel.open(temporary,StandardOpenOption.WRITE)){ByteBuffer data=ByteBuffer.wrap(bytes);while(data.hasRemaining())file.write(data);file.force(true);}
   Files.move(temporary,directory.resolve("request.json"),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
  }finally{Files.deleteIfExists(temporary);}
 }
 private Map<String,Object> read(String name)throws IOException{
  byte[] bytes;
  try(InputStream input=Files.newInputStream(directory.resolve(name),LinkOption.NOFOLLOW_LINKS)){bytes=input.readNBytes(MAX_BYTES+1);}
  catch(NoSuchFileException e){return null;}
  if(bytes.length>MAX_BYTES)throw new IOException("mailbox file exceeds 16 KiB");
  String json=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
  try(JsonReader reader=new JsonReader(new StringReader(json))){
   reader.setLenient(false);Map<String,Object> result=new HashMap<>();reader.beginObject();
   while(reader.hasNext()){
    String key=reader.nextName();if(result.containsKey(key))throw new IOException("duplicate field "+key);
    JsonToken token=reader.peek();Object value;
    if(token==JsonToken.STRING)value=reader.nextString();
    else if(token==JsonToken.BOOLEAN)value=reader.nextBoolean();
    else if(token==JsonToken.NUMBER){String number=reader.nextString();if(!number.matches("0|[1-9][0-9]*"))throw new IOException("integer milliseconds required");value=Long.parseLong(number);}
    else throw new IOException("scalar field required");
    result.put(key,value);
   }
   reader.endObject();if(reader.peek()!=JsonToken.END_DOCUMENT)throw new IOException("trailing JSON");return result;
  }catch(IllegalStateException|NumberFormatException e){throw new IOException("invalid JSON message",e);}
 }
 private static void protocol(Map<String,Object> m)throws IOException{if(number(m,"protocol")!=1)throw new IOException("unsupported protocol");}
 private static long number(Map<String,Object> m,String key)throws IOException{Object v=m.get(key);if(!(v instanceof Long))throw new IOException("integer field required: "+key);return (Long)v;}
 private static boolean bool(Map<String,Object> m,String key)throws IOException{Object v=m.get(key);if(!(v instanceof Boolean))throw new IOException("boolean field required: "+key);return (Boolean)v;}
 private static String string(Map<String,Object> m,String key)throws IOException{Object v=m.get(key);if(!(v instanceof String s)||s.isEmpty())throw new IOException("string field required: "+key);return (String)v;}
}
