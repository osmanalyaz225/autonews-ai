import "dotenv/config";
import express from "express";
import Database from "better-sqlite3";
import OpenAI from "openai";
import webpush from "web-push";
import path from "node:path";
import {fileURLToPath} from "node:url";

const __dirname=path.dirname(fileURLToPath(import.meta.url));
const app=express();
app.use(express.json({limit:"1mb"}));
app.use(express.static(path.join(__dirname,"public")));

const db=new Database(process.env.DB_PATH||"agent.db");
db.pragma("journal_mode=WAL");
db.exec(`
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,due_at TEXT,done INTEGER DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,remind_at TEXT NOT NULL,done INTEGER DEFAULT 0,notified INTEGER DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pushes(id INTEGER PRIMARY KEY AUTOINCREMENT,endpoint TEXT UNIQUE NOT NULL,subscription TEXT NOT NULL,created_at TEXT NOT NULL);
`);

const client=process.env.OPENAI_API_KEY?new OpenAI({apiKey:process.env.OPENAI_API_KEY}):null;
const now=()=>new Date().toISOString();
const secret=process.env.APP_SECRET;

function auth(req,res,next){
  if(!secret)return res.status(503).json({error:"APP_SECRET ayarlanmadı"});
  if((req.headers.authorization||"")!==`Bearer ${secret}`)return res.status(401).json({error:"Yetkisiz"});
  next();
}

if(process.env.VAPID_PUBLIC_KEY&&process.env.VAPID_PRIVATE_KEY&&process.env.VAPID_SUBJECT){
  webpush.setVapidDetails(process.env.VAPID_SUBJECT,process.env.VAPID_PUBLIC_KEY,process.env.VAPID_PRIVATE_KEY);
}

const tools=[
 {type:"function",name:"add_note",description:"Kalıcı not kaydet",parameters:{type:"object",properties:{text:{type:"string"}},required:["text"],additionalProperties:false},strict:true},
 {type:"function",name:"search_notes",description:"Notlarda ara",parameters:{type:"object",properties:{query:{type:"string"}},required:["query"],additionalProperties:false},strict:true},
 {type:"function",name:"add_task",description:"Görev oluştur",parameters:{type:"object",properties:{text:{type:"string"},due_at:{type:["string","null"]}},required:["text","due_at"],additionalProperties:false},strict:true},
 {type:"function",name:"complete_task",description:"Görevi tamamla",parameters:{type:"object",properties:{id:{type:"integer"}},required:["id"],additionalProperties:false},strict:true},
 {type:"function",name:"list_tasks",description:"Görevleri listele",parameters:{type:"object",properties:{done:{type:"boolean"}},required:["done"],additionalProperties:false},strict:true},
 {type:"function",name:"add_reminder",description:"Hatırlatma oluştur. ISO-8601 tarih kullan",parameters:{type:"object",properties:{text:{type:"string"},remind_at:{type:"string"}},required:["text","remind_at"],additionalProperties:false},strict:true},
 {type:"function",name:"list_reminders",description:"Yaklaşan hatırlatmaları listele",parameters:{type:"object",properties:{},additionalProperties:false},strict:true},
 {type:"function",name:"save_memory",description:"Gelecekte faydalı kullanıcı tercihini kaydet. Hassas veri kaydetme",parameters:{type:"object",properties:{text:{type:"string"}},required:["text"],additionalProperties:false},strict:true},
 {type:"function",name:"search_memory",description:"Hafızada ara",parameters:{type:"object",properties:{query:{type:"string"}},required:["query"],additionalProperties:false},strict:true},
 {type:"web_search_preview",search_context_size:"medium"}
];

function callTool(name,a){
 switch(name){
  case"add_note":{const r=db.prepare("INSERT INTO notes(text,created_at) VALUES(?,?)").run(a.text,now());return{ok:true,id:r.lastInsertRowid};}
  case"search_notes":return db.prepare("SELECT * FROM notes WHERE text LIKE ? ORDER BY id DESC LIMIT 20").all(`%${a.query}%`);
  case"add_task":{const r=db.prepare("INSERT INTO tasks(text,due_at,created_at) VALUES(?,?,?)").run(a.text,a.due_at,now());return{ok:true,id:r.lastInsertRowid};}
  case"complete_task":return{ok:db.prepare("UPDATE tasks SET done=1 WHERE id=?").run(a.id).changes>0};
  case"list_tasks":return db.prepare("SELECT * FROM tasks WHERE done=? ORDER BY COALESCE(due_at,'9999'),id DESC LIMIT 50").all(a.done?1:0);
  case"add_reminder":{const r=db.prepare("INSERT INTO reminders(text,remind_at,created_at) VALUES(?,?,?)").run(a.text,a.remind_at,now());return{ok:true,id:r.lastInsertRowid};}
  case"list_reminders":return db.prepare("SELECT * FROM reminders WHERE done=0 ORDER BY remind_at LIMIT 50").all();
  case"save_memory":{const r=db.prepare("INSERT INTO memories(text,created_at) VALUES(?,?)").run(a.text,now());return{ok:true,id:r.lastInsertRowid};}
  case"search_memory":return db.prepare("SELECT * FROM memories WHERE text LIKE ? ORDER BY id DESC LIMIT 20").all(`%${a.query}%`);
  default:throw Error("Bilinmeyen araç");
 }
}

const instructions=`Sen kullanıcının kişisel yapay zeka ajanısın. Türkçe konuş. Kullanıcı bir iş istediğinde mümkünse araçlarını kullanarak gerçekten yap. Not, görev, hatırlatma ve hafıza işlemlerinde araç kullan. Güncel bilgi gerekiyorsa web araması yap. Tarih ve saat belirsizse uydurma. Hassas kişisel verileri hafızaya kaydetme. İşlem tamamlanınca kısa ve net bildir.`;

async function agent(message){
 if(!client)return{reply:"AI motoru bağlı değil. OPENAI_API_KEY ayarlanmalı.",actions:[]};
 let input=[{role:"user",content:message}],actions=[];
 for(let i=0;i<8;i++){
  const r=await client.responses.create({model:process.env.OPENAI_MODEL||"gpt-6-luna",instructions,input,tools});
  const calls=(r.output||[]).filter(x=>x.type==="function_call");
  if(!calls.length)return{reply:r.output_text||"Tamam.",actions};
  for(const c of calls){
   const result=callTool(c.name,JSON.parse(c.arguments||"{}"));
   actions.push({tool:c.name,result});
   input.push(c,{type:"function_call_output",call_id:c.call_id,output:JSON.stringify(result)});
  }
 }
 return{reply:"İşlem tamamlandı.",actions};
}

app.get("/health",(req,res)=>res.json({ok:true,ai:!!client,time:now()}));
app.get("/api/config",auth,(req,res)=>res.json({vapidPublicKey:process.env.VAPID_PUBLIC_KEY||null}));
app.get("/api/state",auth,(req,res)=>res.json({
 notes:db.prepare("SELECT * FROM notes ORDER BY id DESC LIMIT 50").all(),
 tasks:db.prepare("SELECT * FROM tasks ORDER BY done,COALESCE(due_at,'9999'),id DESC LIMIT 50").all(),
 reminders:db.prepare("SELECT * FROM reminders WHERE done=0 ORDER BY remind_at LIMIT 50").all()
}));
app.post("/api/chat",auth,async(req,res)=>{
 try{const m=String(req.body?.message||"").trim();if(!m)return res.status(400).json({error:"message gerekli"});res.json(await agent(m));}
 catch(e){console.error(e);res.status(500).json({error:e.message});}
});
app.post("/api/push/subscribe",auth,(req,res)=>{
 const s=req.body;
 if(!s?.endpoint)return res.status(400).json({error:"subscription gerekli"});
 db.prepare("INSERT INTO pushes(endpoint,subscription,created_at) VALUES(?,?,?) ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription").run(s.endpoint,JSON.stringify(s),now());
 res.json({ok:true});
});
app.post("/api/push/test",auth,async(req,res)=>{
 if(!process.env.VAPID_PUBLIC_KEY)return res.status(503).json({error:"VAPID ayarları yok"});
 let sent=0;
 for(const s of db.prepare("SELECT * FROM pushes").all()){
  try{await webpush.sendNotification(JSON.parse(s.subscription),JSON.stringify({title:"Kişisel AI Ajanı",body:"Bildirim sistemi çalışıyor."}));sent++;}
  catch(e){if([404,410].includes(e.statusCode))db.prepare("DELETE FROM pushes WHERE id=?").run(s.id);}
 }
 res.json({ok:true,sent});
});

async function reminders(){
 if(!process.env.VAPID_PUBLIC_KEY)return;
 const due=db.prepare("SELECT * FROM reminders WHERE done=0 AND notified=0 AND remind_at<=?").all(now());
 const subs=db.prepare("SELECT * FROM pushes").all();
 for(const x of due){
  for(const s of subs){
   try{await webpush.sendNotification(JSON.parse(s.subscription),JSON.stringify({title:"⏰ Hatırlatma",body:x.text}));}
   catch(e){if([404,410].includes(e.statusCode))db.prepare("DELETE FROM pushes WHERE id=?").run(s.id);}
  }
  db.prepare("UPDATE reminders SET notified=1 WHERE id=?").run(x.id);
 }
}

const port=Number(process.env.PORT||3000);
app.listen(port,"0.0.0.0",()=>console.log("Kişisel AI ajanı",port));
setInterval(()=>reminders().catch(console.error),30000);
