package local.zfsbackup;
import java.nio.file.*;
import java.util.*;
import java.io.*;
public final class MailboxTest {
 static int passed;
 static void check(boolean b,String s){if(!b)throw new AssertionError(s);}
 static void rejects(Mailbox m,Path p,String json)throws Exception{Files.writeString(p,json);try{m.heartbeat();throw new AssertionError("must reject: "+json.substring(0,Math.min(80,json.length())));}catch(IOException expected){passed++;}}
 public static void main(String[] args)throws Exception{
  Path dir=Files.createTempDirectory("zfs-mailbox-test");Mailbox m=new Mailbox(dir);Path hb=dir.resolve("heartbeat.json");
  try{
   check(m.heartbeat()==null,"missing daemon is not ready");
   Files.writeString(hb,"{\"protocol\":1,\"updated_ms\":1000,\"ready\":true}");
   check(m.heartbeat()!=null&&m.heartbeat().ready()&&m.heartbeat().updated()==1000,"valid heartbeat");passed++;
   for(String bad:List.of("{}","{\"protocol\":2,\"updated_ms\":1000,\"ready\":true}","{\"protocol\":1,\"updated_ms\":1.5,\"ready\":true}","{\"protocol\":1,\"updated_ms\":\"1000\",\"ready\":true}","{\"protocol\":1,\"protocol\":1,\"updated_ms\":1000,\"ready\":true}","{protocol:1,updated_ms:1000,ready:true}","{\"protocol\":1,\"updated_ms\":1000,\"ready\":\"true\"}"," ".repeat(16385)))rejects(m,hb,bad);
   UUID id=UUID.randomUUID();Checkpoint.Request request=new Checkpoint.Request(id,"COMMIT",1000,16000,1200);m.publish(request);
   String json=Files.readString(dir.resolve("request.json"));check(json.contains("\"committed_ms\":1200")&&json.contains(id.toString())&&json.contains("\"deadline_ms\":16000"),"exact request fields");passed++;
   Files.writeString(dir.resolve("response.json"),"{\"protocol\":1,\"id\":\""+id+"\",\"phase\":\"SNAPSHOT_READY\",\"updated_ms\":1100,\"snapshot\":\"mc-hourly-test\"}");
   check(m.response().id().equals(id)&&m.response().phase().equals("SNAPSHOT_READY"),"valid response");passed++;
   Files.writeString(dir.resolve("response.json"),"{\"protocol\":1,\"id\":\"1-1-1-1-1\",\"phase\":\"COMMITTED\",\"updated_ms\":1100,\"snapshot\":\"test\"}");
   try{m.response();throw new AssertionError("canonical UUID required");}catch(IOException expected){passed++;}
   try(var files=Files.list(dir)){check(files.noneMatch(p->p.toString().endsWith(".tmp")),"no temp files after atomic publication");}passed++;
   System.out.println(passed+" transport checks passed");
  }finally{try(var files=Files.list(dir)){for(Path p:files.toList())Files.delete(p);}Files.delete(dir);}
 }
}
