package local.zfsbackup;
import java.util.*;
/** Render selected business fields; never dump agent JSON or credentials into chat/logs. */
final class AgentView {
 static String status(Map<String,Object> value){
  String result="Agent ready="+Boolean.TRUE.equals(value.get("ready"))+"; checkpoint="+field(object(value.get("checkpoint")),"state","idle");
  result+="; last snapshot="+field(object(value.get("last_success")),"snapshot","none");
  Map<String,Object> job=object(value.get("rollback"));if(!job.isEmpty())result+="; "+rollback(job);
  if(value.get("last_error")!=null)result+="; agent reports a previous error (inspect agent logs)";
  return result;
 }
 static String snapshots(Map<String,Object> value){
  if(!(value.get("snapshots") instanceof List<?> snapshots))return "Invalid snapshot listing from agent.";
  if(snapshots.isEmpty())return "No managed published snapshots.";
  StringJoiner lines=new StringJoiner("\n");int shown=0;
  for(Object item:snapshots){if(shown++>=24)break;Map<String,Object> point=object(item);lines.add(field(point,"name","invalid")+" ["+field(point,"event","unknown")+"] GUID="+field(point,"guid","unknown"));}
  return lines.toString();
 }
 static String rollback(Map<String,Object> value){
  return "Rollback "+field(value,"id","unknown")+": "+field(value,"phase",field(value,"state","unknown"))+"; snapshot="+field(value,"snapshot","unknown");
 }
 private static String field(Map<String,Object> value,String key,String fallback){
  if(!(value.get(key) instanceof String text)||text.isEmpty())return fallback;
  String safe=text.replaceAll("[^a-zA-Z0-9_.: -]","?");return safe.substring(0,Math.min(200,safe.length()));
 }
 @SuppressWarnings("unchecked") private static Map<String,Object> object(Object value){return value instanceof Map<?,?> map?(Map<String,Object>)map:Map.of();}
}
