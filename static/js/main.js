(() => {
 const root=document.documentElement;
 const saved=localStorage.getItem("keepsake-theme");
 if(saved) root.dataset.theme=saved;
 const toggle=document.getElementById("themeToggle");
 if(toggle) toggle.addEventListener("click",()=>{
   const next=root.dataset.theme==="dark"?"light":"dark";
   root.dataset.theme=next; localStorage.setItem("keepsake-theme",next);
   fetch("/settings",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"theme="+next});
 });
 const editor=document.getElementById("editor"), field=document.getElementById("contentField");
 if(editor && field){
   const count=()=>{const t=editor.innerText.trim();const words=t?t.split(/\s+/).length:0;document.getElementById("wordCount").textContent=`${words} words · ${t.length} characters`;field.value=editor.innerHTML;};
   editor.addEventListener("input",count); count();
   document.querySelectorAll("[data-cmd]").forEach(b=>b.addEventListener("click",()=>{
     editor.focus();document.execCommand(b.dataset.cmd,false,b.dataset.value||null);count();
   }));
   document.getElementById("entryForm").addEventListener("submit",count);
 }
})();
