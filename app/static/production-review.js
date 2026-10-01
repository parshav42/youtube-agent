"use strict";
const form=document.getElementById("auth");
let reviewToken=""; let productionId="";
form.addEventListener("submit",async event=>{
  event.preventDefault();
  const token=document.getElementById("token").value;
  const id=document.getElementById("id").value.trim();
  reviewToken=token; productionId=id;
  const headers={Authorization:`Bearer ${token}`};
  const error=document.getElementById("error"); error.textContent="";
  try{
    const response=await fetch(`/api/productions/${encodeURIComponent(id)}/review`,{headers});
    if(!response.ok) throw new Error(response.status===401?"Authorization failed.":response.status===404?"Production was not found.":"Review data could not be loaded.");
    const data=await response.json();
    for(const [selector,value] of [["#title",data.title||"Untitled production"],["#script",data.script?.narration||"Script unavailable"],["#description",data.description||"Description unavailable"],["#status",data.status],["#audio",JSON.stringify(data.audio_qa||{technical_status:"not run"},null,2)],["#videoqa",JSON.stringify(data.video_qa||{technical_status:"not run"},null,2)],["#findings",(data.findings||[]).join("\n")||"No validation findings."]]) document.querySelector(selector).textContent=value;
    const media=await fetch(data.video_url,{headers});
    if(!media.ok) throw new Error("Final video preview is unavailable.");
    const video=document.getElementById("video");
    if(video.dataset.objectUrl) URL.revokeObjectURL(video.dataset.objectUrl);
    video.dataset.objectUrl=URL.createObjectURL(await media.blob()); video.src=video.dataset.objectUrl;
    document.getElementById("content").hidden=false;
  }catch(cause){error.textContent=cause instanceof Error?cause.message:"Review request failed.";}
});
for(const decision of ["approve","reject"]) document.getElementById(decision).addEventListener("click",async()=>{
  const reviewer=document.getElementById("reviewer").value.trim();
  const error=document.getElementById("error");
  if(!reviewer){error.textContent="Enter a reviewer name.";return;}
  if(!window.confirm(`Confirm ${decision} for production ${productionId}?`)) return;
  try{
    const response=await fetch(`/api/productions/${encodeURIComponent(productionId)}/review/${decision}`,{method:"POST",headers:{Authorization:`Bearer ${reviewToken}`,"Content-Type":"application/json"},body:JSON.stringify({confirm_review:true,reviewer})});
    if(!response.ok) throw new Error(response.status===409?"A review decision exists or QA blocks approval.":"Review decision could not be recorded.");
    const result=await response.json(); document.getElementById("status").textContent=result.status;
    document.getElementById("approve").disabled=true; document.getElementById("reject").disabled=true; error.textContent="Decision recorded.";
  }catch(cause){error.textContent=cause instanceof Error?cause.message:"Review request failed.";}
});
