
let master={customers:[],fabrics:[],colors:[]};
const $=id=>document.getElementById(id);
const qs=sel=>document.querySelector(sel);

async function request(url,opt={}){
  const r=await fetch(url,opt);
  const j=await r.json();
  if(!r.ok) throw new Error(j.error||"Error");
  return j;
}
async function load(){
  try{
    master=await request("/api/master");
    render();
  }catch(err){
    console.error(err);
    alert("Gagal memuat database: "+err.message);
  }
}
function rowText(kind,x){
  if(kind==="customers") return typeof x==="object" ? x.name : x;
  return typeof x==="object" ? (x.name||x.value||"") : String(x||"");
}
function renderList(kind,listId,countId,query=""){
  const q=query.toLowerCase();
  const arr=(master[kind]||[]).filter(x=>rowText(kind,x).toLowerCase().includes(q));
  $(countId).textContent=`(${(master[kind]||[]).length})`;
  $(listId).innerHTML=arr.map(x=>{
    if(kind==="customers"){
      return `<div class="db-row"><span>${x.name}</span><button data-delete-kind="customers" data-customer-id="${x.id}">Hapus</button></div>`;
    }
    const val=rowText(kind,x); return `<div class="db-row"><span>${val}</span><button data-delete-kind="${kind}" data-value="${encodeURIComponent(val)}">Hapus</button></div>`;
  }).join("") || `<div class="muted">Tidak ada data.</div>`;
}
function render(){
  const stat=document.getElementById("masterCustomerStat");
  if(stat) stat.textContent=(master.customers||[]).length;
  const c=qs('[data-search="customers"]');
  const f=qs('[data-search="fabrics"]');
  const w=qs('[data-search="colors"]');

  renderList("customers","customersList","customerCount",c ? c.value : "");
  renderList("fabrics","fabricsList","fabricCount",f ? f.value : "");
  renderList("colors","colorsList","colorCount",w ? w.value : "");
}
document.addEventListener("input",e=>{
  const s=e.target.closest("[data-search]");
  if(s) render();
});
document.addEventListener("click",async e=>{
  const add=e.target.closest("[data-add]");
  if(add){
    const kind=add.dataset.add;
    const input=kind==="customers"?$("newCustomer"):kind==="fabrics"?$("newFabric"):$("newColor");
    const value=input.value.trim();
    if(!value)return;
    try{
      await request(`/api/master/${kind}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({value})});
      input.value=""; await load();
    }catch(err){alert(err.message)}
    return;
  }

  const del=e.target.closest("[data-delete-kind]");
  if(del){
    const kind=del.dataset.deleteKind;
    if(!confirm("Hapus data ini?"))return;
    let url=`/api/master/${kind}`;
    if(kind==="customers") url+=`?customer_id=${del.dataset.customerId}`;
    else url+=`?value=${del.dataset.value}`;
    try{await request(url,{method:"DELETE"});await load()}catch(err){alert(err.message)}
  }
});
load();
