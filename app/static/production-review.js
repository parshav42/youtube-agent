"use strict";
const form=document.getElementById("auth");
form.addEventListener("submit",async event=>{
  event.preventDefault();
  const token=document.getElementById("token").value;
  const id=document.getElementById("id").value.trim();
  const headers={Authorization:`Bearer ${token}`};
  const error=document.getElementById("error"); error.textContent="";
  try{
    const response=await fetch(`/api/productions/${encodeURIComponent(id)}/review`,{headers});
    if(!response.ok) throw new Error(response.status===401?"Authorization failed.":response.status===404?"Production was not found.":"Review data could not be loaded.");
    const data=await response.json();
    for(const [selector,value] of [["#title",data.title||"Untitled production"],["#script",data.script?.narration||"Script unavailable"],["#description",data.description||"Description unavailable"],["#status",data.status],["#audio",JSON.stringify(data.audio_qa||{technical_status:"not run"},null,2)],["#videoqa",JSON.stringify(data.video_qa||{technical_status:"not run"},null,2)],["#findings",(data.findings||[]).join("\n")||"No validation findings."]]) document.querySelector(selector).textContent=value;
    const video=document.getElementById("video"); video.src=`${data.video_url}`; video.onerror=()=>{error.textContent="Final video preview is unavailable.";};
    document.getElementById("content").hidden=false;
  }catch(cause){error.textContent=cause instanceof Error?cause.message:"Review request failed.";}
});
