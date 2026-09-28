let orders=[],master={customers:[],fabrics:[],colors:[]},items=[],payments=[],editing=null,pendingFiles=[];

const MAX_QTY_KG=10000;
const MAX_QTY_ROLL=10000;
const MAX_HARGA_PER_KG=150000;
function normalizePrice(v){
 const n=Number(v||0);
 if(!Number.isFinite(n)||n<0)return 0;
 return Math.round(n);
}

function clampNumber(value,min,max){
 const n=Number(value||0);
 if(!Number.isFinite(n))return min;
 return Math.min(Math.max(n,min),max);
}
let noPoSort="az",currentPage=1;const pageSize=10,selectedOrderIds=new Set(),$=id=>document.getElementById(id);
const rp=n=>new Intl.NumberFormat("id-ID",{style:"currency",currency:"IDR",maximumFractionDigits:0}).format(Number(n||0));
const fmtDate=v=>v?new Date(v+"T00:00:00").toLocaleDateString("id-ID"):"-";
const fmtDT=v=>v?new Date(v).toLocaleString("id-ID"):"-";
function todayISOLocal(){const d=new Date();const local=new Date(d.getTime()-d.getTimezoneOffset()*60000);return local.toISOString().slice(0,10)}
const totalItem=i=>Number(i.qty_kg||0)*Number(i.harga_per_kg||0);
async function api(url,opt={}){const r=await fetch(url,opt);let j={};try{j=await r.json()}catch{}if(r.status===401){location.href="/login";throw new Error("Sesi berakhir")}if(!r.ok)throw new Error(j.error||"Error");return j}
function esc(v){return String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
function statusBadge(s){return `<span class="badge">${esc(s)}</span>`}
function calc(){const total=items.reduce((a,x)=>a+totalItem(x),0),dp=payments.reduce((a,x)=>a+Number(x.nominal||0),0);$("sTotal").textContent=rp(total);$("sDp").textContent=rp(dp);$("sSisa").textContent=rp(Math.max(total-dp,0));const dp30=Math.round(total*.30);if($("dp30Info"))$("dp30Info").textContent=total>0?`30% = ${rp(dp30)}`:"30% dari total pesanan";if($("tanggal").value){const d=new Date($("tanggal").value+"T00:00:00");d.setDate(d.getDate()+Number($("estimasi").value||21));$("sTarget").textContent=d.toLocaleDateString("id-ID")}}
function renderItems(){$("items").innerHTML=items.map((i,n)=>`<article class="material-card" data-row="${n}"><div class="material-card-head"><div><span class="material-number">BAHAN ${n+1}</span><strong>${esc(i.jenis_kain||"Belum dipilih")}${i.warna?" • "+esc(i.warna):""}</strong></div><button type="button" class="remove-material" data-del-item="${n}">Hapus</button></div><div class="material-fields"><label><span>Jenis Kain</span><input data-f="jenis_kain" list="fabricList" value="${esc(i.jenis_kain||"")}"></label><label><span>Warna</span><input data-f="warna" list="colorList" value="${esc(i.warna||"")}"></label><label><span>Qty KG</span><input data-f="qty_kg" type="number" min="0" max="10000" step=".01" value="${i.qty_kg||""}"></label><label><span>Qty Roll</span><input data-f="qty_roll" type="number" min="0" max="10000" step=".01" value="${i.qty_roll||""}"></label><label><span>Harga / KG</span><input data-f="harga_per_kg" type="number" min="0" max="150000" value="${i.harga_per_kg||""}"></label><div class="item-total-card"><span>Total Item</span><strong>${rp(totalItem(i))}</strong><small>Qty KG × Harga/KG</small></div></div></article>`).join("");calc()}
function renderPayments(){
 const rows=payments.map((p,n)=>{
   let actions="";
   if(!p.id){
     actions=`<button type="button" class="pay-action danger" data-remove-draft="${n}">Hapus Draft</button>`;
   }else if(window.APP_IS_SERVER){
     actions=`<button type="button" class="pay-action" data-correct-pay="${n}">Koreksi</button>
              <button type="button" class="pay-action danger" data-cancel-pay="${n}">Batalkan</button>`;
   }else if(p.can_undo){
     actions=`<button type="button" class="pay-action warning" data-undo-pay="${n}">Undo (${Number(p.undo_seconds_left||0)} dtk)</button>`;
   }else{
     actions=`<button type="button" class="pay-action" data-request-correction="${n}">Ajukan Koreksi</button>`;
   }

   const source=p.id
     ? `<small>${fmtDT(p.created_at)}${p.created_by?` • ${esc(p.created_by)}`:""}</small>`
     : `<small>Draft • belum disimpan</small>`;

   return `<div class="pay-row">
     <div><b>${rp(p.nominal)}</b>${source}</div>
     <div class="pay-actions">${actions}</div>
   </div>`;
 }).join("");

 $("payments").innerHTML=rows||'<div class="empty-inline">Belum ada pembayaran DP.</div>';
 calc();
}
function renderGallery(){const existing=editing?.images||[];$("gallery").innerHTML=existing.map(x=>`<div class="photo-card"><img class="photo-preview-trigger" src="/static/uploads/${x.filename}" data-photo-src="/static/uploads/${x.filename}" data-photo-filename="${esc(x.filename||"foto-po.jpg")}" data-photo-title="${esc(editing?.no_po||"Foto Bukti")}" data-photo-group="po-gallery"><button type="button" data-del-img="${x.id}">×</button></div>`).join("")+pendingFiles.map((f,n)=>{const previewUrl=URL.createObjectURL(f);return `<div class="photo-card"><img class="photo-preview-trigger" src="${previewUrl}" data-photo-src="${previewUrl}" data-photo-filename="${esc(f.name||"foto-po.jpg")}" data-photo-title="Preview Foto" data-photo-group="po-gallery"><button type="button" data-remove-pending="${n}">×</button></div>`}).join("");$("photoCount").textContent=`${existing.length+pendingFiles.length} foto`}
function resetForm(){editing=null;items=[{jenis_kain:"",warna:"",qty_kg:0,qty_roll:0,harga_per_kg:0}];payments=[];pendingFiles=[];$("editId").value="";$("noPo").value="Otomatis saat disimpan";$("tanggal").value=todayISOLocal();$("customer").value="";$("estimasi").value=21;$("status").value="PROSES";$("catatan").value="";$("images").value="";renderItems();renderPayments();renderGallery();applyDoneLock(null)}

function applyDoneLock(o){
 const locked=!!(o && String(o.status||"").toUpperCase()==="DONE" && !window.APP_IS_SERVER);
 const dlg=$("dialog");
 if(!dlg)return;

 dlg.classList.toggle("readonly-done",locked);

 // No PO selalu read-only dan Tanggal Dibuat selalu otomatis dari sistem.
 const noPo=$("noPo");
 if(noPo)noPo.disabled=true;
 const tanggal=$("tanggal");
 if(tanggal){
   tanggal.readOnly=true;
   tanggal.disabled=locked;
   tanggal.title="Tanggal dibuat otomatis oleh sistem dan tidak dapat diubah manual.";
 }

 // Semua field lain hanya dikunci jika status DONE.
 const controls=dlg.querySelectorAll("input,select,textarea,button");
 controls.forEach(el=>{
   if(el.id==="noPo"||el.id==="tanggal")return;
   if(el.id==="closeBtn"||el.id==="cancelBtn")return;

   if(locked){
     el.disabled=true;
   }else{
     // Status selain DONE harus kembali editable.
     el.disabled=false;
   }
 });

 // Tombol close/cancel selalu aktif.
 if($("closeBtn"))$("closeBtn").disabled=false;
 if($("cancelBtn"))$("cancelBtn").disabled=false;

 let note=dlg.querySelector("#doneLockNotice");
 if(locked){
   if(!note){
     note=document.createElement("div");
     note.id="doneLockNotice";
     note.className="done-lock-notice";
     note.textContent="🔒 PO berstatus DONE. ADMIN hanya dapat melihat. Hanya SERVER yang dapat melakukan perubahan.";
     const form=dlg.querySelector("form")||dlg.querySelector(".modal-card")||dlg;
     form.prepend(note);
   }
 }else if(note){
   note.remove();
 }
}

function loadForm(o){editing=o;items=o.items.map(x=>({...x,harga_per_kg:normalizePrice(x.harga_per_kg),_price_changed:false}));payments=o.payments.map(x=>({...x}));pendingFiles=[];$("editId").value=o.id;$("noPo").value=o.no_po;$("tanggal").value=o.tanggal;$("customer").value=o.customer;$("estimasi").value=o.estimasi_hari;$("status").value=o.status;$("catatan").value=o.catatan||"";renderItems();renderPayments();renderGallery();applyDoneLock(o)}
async function loadAll(){[orders,master]=await Promise.all([api("/api/orders"),api("/api/master")]);$("customerList").innerHTML=(master.customers||[]).map(x=>`<option value="${esc(typeof x==="object"?x.name:x)}">`).join("");$("fabricList").innerHTML=(master.fabrics||[]).map(x=>`<option value="${esc(typeof x==="object"?x.name:x)}">`).join("");$("colorList").innerHTML=(master.colors||[]).map(x=>`<option value="${esc(typeof x==="object"?x.name:x)}">`).join("");renderTable()}
function renderPagination(total){const pages=Math.max(1,Math.ceil(total/pageSize));currentPage=Math.min(Math.max(1,currentPage),pages);const from=total?((currentPage-1)*pageSize)+1:0,to=total?Math.min(currentPage*pageSize,total):0;$("pageInfo").textContent=`Showing ${from} to ${to} of ${total} entries`;const a=[`<button class="dt-page-btn" data-nav="prev" ${currentPage===1?"disabled":""}>Previous</button>`];for(let p=1;p<=pages;p++)a.push(`<button class="dt-page-btn ${p===currentPage?"active":""}" data-page="${p}">${p}</button>`);a.push(`<button class="dt-page-btn" data-nav="next" ${currentPage===pages?"disabled":""}>Next</button>`);$("pagination").innerHTML=a.join("")}

function editBadge(o){
 const n=Number(o.edit_count||0);
 if(!n)return "";
 return `<button class="edit-badge" type="button" data-history="${o.id}" title="Lihat riwayat perubahan">✏️ Edited ${n}x</button>`;
}
async function showOrderHistory(orderId){
 try{
   const data=await api("/api/orders/"+orderId+"/history");
   const o=data.order||{};
   const history=data.history||[];

   $("historyTitle").textContent=`Riwayat Perubahan • ${o.no_po||""}`;
   $("historyMeta").innerHTML=`
     <div class="history-summary">
       <div><small>Total Edit</small><b>${Number(o.edit_count||0)}x</b></div>
       <div><small>Terakhir Diubah</small><b>${esc(o.last_edited_by||"-")}</b></div>
       <div><small>Waktu</small><b>${o.last_edited_at?fmtDT(o.last_edited_at):"-"}</b></div>
     </div>`;

   $("historyList").innerHTML=history.length?history.map((h,idx)=>{
     const changes=(h.detail&&Array.isArray(h.detail.changes))?h.detail.changes:[];
     const d=h.detail||{};
     let dpDetail="";
     if(["ADD_DP","UNDO_DP","CANCEL_DP","CORRECT_DP","REQUEST_DP_CORRECTION","APPROVE_DP_CORRECTION","REJECT_DP_CORRECTION"].includes(h.action)){
       const labels={
         ADD_DP:"DP ditambahkan",
         UNDO_DP:"DP di-undo",
         CANCEL_DP:"DP dibatalkan",
         CORRECT_DP:"DP dikoreksi",
         REQUEST_DP_CORRECTION:"Pengajuan koreksi DP",
         APPROVE_DP_CORRECTION:"Koreksi DP disetujui",
         REJECT_DP_CORRECTION:"Koreksi DP ditolak"
       };
       dpDetail=`<div class="history-change-list">
         <div class="history-change"><b>${labels[h.action]||esc(h.action)}</b><span>
           ${d.nominal!=null?rp(d.nominal):""}
           ${d.before!=null||d.requested!=null||d.after!=null?`${d.before!=null?rp(d.before):""}${d.before!=null?" → ":""}${d.after!=null?rp(d.after):(d.requested!=null?rp(d.requested):"")}`:""}
         </span></div>
         ${d.reason?`<div class="history-change"><b>Alasan</b><span>${esc(d.reason)}</span></div>`:""}
         ${d.note?`<div class="history-change"><b>Catatan SERVER</b><span>${esc(d.note)}</span></div>`:""}
       </div>`;
     }
     return `<div class="history-entry">
       <div class="history-entry-head">
         <div><b>${esc(h.actor||"-")}</b> <span class="role-mini">${esc(h.role||"")}</span> <span class="role-mini">${esc(h.action||"")}</span></div>
         <small>${h.created_at?fmtDT(h.created_at):"-"}</small>
       </div>
       ${dpDetail || (changes.length?`<div class="history-change-list">${changes.map(c=>`
         <div class="history-change">
           <b>${esc(c.field||"Perubahan")}</b>
           <span>${esc(c.before??"-")} → ${esc(c.after??"-")}</span>
         </div>`).join("")}</div>`:`<div class="history-change muted">Perubahan tercatat.</div>`)}
     </div>`;
   }).join(""):`<div class="empty-state">Belum ada riwayat perubahan.</div>`;

   const modal=$("historyModal");
   modal.classList.add("show");
   document.body.classList.add("history-modal-open");
 }catch(err){
   alert("Riwayat perubahan gagal dibuka: "+err.message);
 }
}
function renderTable(){const q=$("search").value.trim().toLowerCase(),sf=$("statusFilter").value;let list=orders.filter(o=>(!sf||o.status===sf)&&(!q||[o.no_po,o.customer,...o.items.flatMap(i=>[i.jenis_kain,i.warna])].join(" ").toLowerCase().includes(q)));list.sort((a,b)=>noPoSort==="az"?String(a.no_po).localeCompare(String(b.no_po),"id",{numeric:true}):String(b.no_po).localeCompare(String(a.no_po),"id",{numeric:true}));const pages=Math.max(1,Math.ceil(list.length/pageSize));if(currentPage>pages)currentPage=pages;const shown=list.slice((currentPage-1)*pageSize,currentPage*pageSize);$("tbody").innerHTML=shown.map(o=>`<tr><td><div class="no-po-cell">${window.APP_IS_SERVER?`<input class="row-checkbox order-select" type="checkbox" data-select-id="${o.id}" ${selectedOrderIds.has(Number(o.id))?"checked":""}>`:""}<b>${esc(o.no_po)}</b>${editBadge(o)}</div></td><td>${fmtDT(o.created_at)}</td><td>${esc(o.customer)}</td><td>${o.items.map(i=>`<div><b>${esc(i.jenis_kain)}</b> • ${esc(i.warna)}<br><small>${i.qty_kg} KG • ${i.qty_roll} Roll × ${rp(i.harga_per_kg)}</small></div>`).join("")}</td><td>${o.total_qty_kg} KG</td><td>${o.total_roll} Roll</td><td><b>${rp(o.total)}</b></td><td>${rp(o.total_dp)}</td><td>${rp(o.sisa)}</td><td>${fmtDate(o.target)}</td><td>${statusBadge(o.status)}</td><td>${o.images.slice(0,3).map(x=>`<img class="thumb photo-preview-trigger" src="/static/uploads/${x.filename}" data-photo-src="/static/uploads/${x.filename}" data-photo-filename="${esc(x.filename||"foto-po.jpg")}" data-photo-group="${o.id}">`).join("")}</td><td><button class="action-trigger" data-action-menu="${o.id}">Aksi</button></td></tr>`).join("");$("kTotal").textContent=orders.length;$("kProses").textContent=orders.filter(o=>o.status==="PROSES").length;$("kDpMasuk").textContent=orders.filter(o=>o.status==="DP MASUK").length;$("kDone").textContent=orders.filter(o=>o.status==="DONE").length;$("kSisa").textContent=rp(orders.reduce((a,o)=>a+o.sisa,0));renderPagination(list.length);updateBulkUI()}
function updateBulkUI(){if(!window.APP_IS_SERVER)return;const n=selectedOrderIds.size;$("deleteSelectedBtn").textContent=`Hapus Dipilih (${n})`;$("deleteSelectedBtn").disabled=!n;const c=[...document.querySelectorAll(".order-select")],m=$("selectAllRows");m.checked=!!c.length&&c.every(x=>x.checked);m.indeterminate=c.some(x=>x.checked)&&!m.checked}
function waText(o){const a=[`📦 *PESANAN PO AN OTS*`,`No PO: *${o.no_po}*`,`Customer: *${o.customer}*`,`Status: *${o.status}*`,`Target: ${fmtDate(o.target)}`,"","*DETAIL PESANAN*"];o.items.forEach((i,n)=>a.push(`${n+1}. *${i.jenis_kain}* - ${i.warna}\n   ${i.qty_kg} KG • ${i.qty_roll} Roll • ${rp(i.harga_per_kg)}/KG\n   Total: *${rp(totalItem(i))}*`));a.push("",`Total: *${rp(o.total)}*`,`DP: *${rp(o.total_dp)}*`,`Sisa: *${rp(o.sisa)}*`);return a.join("\n")}
function showToast(message,type="ok"){
 let t=document.getElementById("appToast");
 if(!t){
   t=document.createElement("div");
   t.id="appToast";
   t.className="app-toast";
   document.body.appendChild(t);
 }
 t.className="app-toast "+(type==="error"?"error":"ok");
 t.textContent=message;
 t.hidden=false;
 clearTimeout(showToast._timer);
 showToast._timer=setTimeout(()=>{t.hidden=true},2200);
}

async function copyTextAuto(text){
 try{
   if(navigator.clipboard && window.isSecureContext){
     await navigator.clipboard.writeText(text);
     return true;
   }
 }catch(e){}

 // Fallback untuk akses lokal HTTP/IP yang kadang memblokir Clipboard API.
 const ta=document.createElement("textarea");
 ta.value=text;
 ta.setAttribute("readonly","");
 ta.style.position="fixed";
 ta.style.opacity="0";
 ta.style.pointerEvents="none";
 ta.style.left="-9999px";
 document.body.appendChild(ta);
 ta.focus();
 ta.select();
 ta.setSelectionRange(0,ta.value.length);
 let ok=false;
 try{ok=document.execCommand("copy")}catch(e){ok=false}
 ta.remove();
 return ok;
}

function validPhotoFiles(fileList){
 const allowed=["image/jpeg","image/png","image/webp"];
 return [...fileList].filter(f=>{
   const ext=(f.name.split(".").pop()||"").toLowerCase();
   return allowed.includes(f.type)||["jpg","jpeg","png","webp"].includes(ext);
 });
}

function addPhotoFiles(fileList){
 const accepted=validPhotoFiles(fileList);
 if(!accepted.length){
   showToast("File bukan format foto yang didukung.","error");
   return;
 }
 pendingFiles=[...pendingFiles,...accepted];
 renderGallery();
 showToast(`${accepted.length} foto ditambahkan.`);
}


$("customer").addEventListener("input",e=>{
 if(e.target.value.length>30){
   e.target.value=e.target.value.slice(0,30);
   showToast("Nama customer maksimal 30 karakter.","error");
 }
});
$("addBtn").onclick=()=>{resetForm();$("dialog").showModal()};$("closeBtn").onclick=$("cancelBtn").onclick=()=>$("dialog").close();$("addItem").onclick=()=>{items.push({jenis_kain:"",warna:"",qty_kg:0,qty_roll:0,harga_per_kg:0,_price_changed:true});renderItems()};
$("items").addEventListener("input",e=>{
 const row=e.target.closest("[data-row]");
 if(!row||!e.target.dataset.f)return;
 const n=Number(row.dataset.row),f=e.target.dataset.f,v=e.target.value;
 if(["qty_kg","qty_roll","harga_per_kg"].includes(f)){
   let num=v===""?0:Number(v);

   if(f==="qty_kg" && num>MAX_QTY_KG){
     num=MAX_QTY_KG;
     e.target.value=MAX_QTY_KG;
     showToast("QTY KG maksimal 10.000 KG.","error");
   }

   if(f==="qty_roll" && num>MAX_QTY_ROLL){
     num=MAX_QTY_ROLL;
     e.target.value=MAX_QTY_ROLL;
     showToast("QTY Roll maksimal 10.000 Roll.","error");
   }

   if(f==="harga_per_kg" && num>MAX_HARGA_PER_KG){
     num=MAX_HARGA_PER_KG;
     e.target.value=MAX_HARGA_PER_KG;
     showToast("Harga/KG maksimal Rp150.000.","error");
   }

   if(num<0){
     num=0;
     e.target.value=0;
   }

   if(f==="harga_per_kg"){
     num=normalizePrice(num);
     items[n]._price_changed=true;
   }
   items[n][f]=num;
 }else{
   items[n][f]=v;
 }

 // Jangan render ulang seluruh form saat sedang mengetik.
 // Ini membuat angka seperti 100000 bisa diketik langsung tanpa input ter-reset tiap digit.
 if(f==="qty_kg"){
   items[n].qty_roll=items[n].qty_kg?Math.min(MAX_QTY_ROLL,Number((items[n].qty_kg/25).toFixed(2))):0;
   const roll=row.querySelector('[data-f="qty_roll"]');
   if(roll && document.activeElement!==roll) roll.value=items[n].qty_roll||"";
 }
 if(f==="qty_roll"){
   items[n].qty_kg=items[n].qty_roll?Math.min(MAX_QTY_KG,Number((items[n].qty_roll*25).toFixed(2))):0;
   const kg=row.querySelector('[data-f="qty_kg"]');
   if(kg && document.activeElement!==kg) kg.value=items[n].qty_kg||"";
 }

 // Update total item dan ringkasan tanpa mengganggu fokus/cursor input.
 const totalCard=row.querySelector(".item-total-card strong");
 if(totalCard) totalCard.textContent=rp(totalItem(items[n]));
 calc();
});
$("items").addEventListener("change",e=>{
 const row=e.target.closest("[data-row]");
 if(!row||!e.target.dataset.f)return;
 const n=Number(row.dataset.row);
 if(e.target.dataset.f==="harga_per_kg"){
   items[n].harga_per_kg=normalizePrice(items[n].harga_per_kg);
   e.target.value=items[n].harga_per_kg||"";
   items[n]._price_changed=true;
   const totalCard=row.querySelector(".item-total-card strong");
   if(totalCard) totalCard.textContent=rp(totalItem(items[n]));
   calc();
 }
 const strong=row.querySelector(".material-card-head strong");
 if(strong){
   const i=items[n];
   strong.textContent=(i.jenis_kain||"Belum dipilih")+(i.warna?" • "+i.warna:"");
 }
});

$("items").addEventListener("click",e=>{const b=e.target.closest("[data-del-item]");if(b){items.splice(Number(b.dataset.delItem),1);if(!items.length)items.push({jenis_kain:"",warna:"",qty_kg:0,qty_roll:0,harga_per_kg:0});renderItems()}});
function currentOrderTotal(){
 return items.reduce((a,x)=>a+totalItem(x),0);
}
function currentDpTotal(){
 return payments.reduce((a,x)=>a+Number(x.nominal||0),0);
}
function remainingDpCapacity(){
 return Math.max(currentOrderTotal()-currentDpTotal(),0);
}
function validateDpAddition(nominal){
 const total=currentOrderTotal();
 const current=currentDpTotal();
 if(total<=0){
   alert("Total pesanan masih Rp0. Isi QTY dan Harga/KG terlebih dahulu.");
   return false;
 }
 if(current+Number(nominal||0)>total+.01){
   const remaining=Math.max(total-current,0);
   const requested=Number(nominal||0);
   alert(
     `⚠️ DP MELEBIHI TOTAL HARGA\n\n`+
     `DP tidak dapat ditambahkan karena total pembayaran akan melebihi harga pesanan.\n\n`+
     `Total Pesanan : ${rp(total)}\n`+
     `DP Sebelumnya : ${rp(current)}\n`+
     `DP yang Ditambah : ${rp(requested)}\n`+
     `Maksimal DP Tambahan : ${rp(remaining)}\n\n`+
     `Silakan masukkan nominal DP maksimal ${rp(remaining)}.`
   );
   const input=$("newDp");
   if(input){
     input.value=remaining>0?new Intl.NumberFormat("id-ID").format(Math.floor(remaining)):"";
     input.focus();
     input.select?.();
   }
   return false;
 }
 return true;
}

$("dp30Btn").onclick=()=>{
 const total=items.reduce((a,x)=>a+totalItem(x),0);
 if(total<=0){
   alert("Isi QTY dan Harga/KG terlebih dahulu agar total pesanan dapat dihitung.");
   return;
 }
 const nominal=Math.round(total*.30);
 const remaining=remainingDpCapacity();
 if(nominal>remaining+.01){
   alert(`DP 30% (${rp(nominal)}) melebihi sisa tagihan yang dapat dicatat (${rp(remaining)}).`);
   $("newDp").value=remaining>0?new Intl.NumberFormat("id-ID").format(Math.floor(remaining)):"";
   return;
 }
 $("newDp").value=new Intl.NumberFormat("id-ID").format(nominal);
 showToast(`DP 30% otomatis: ${rp(nominal)}`,"ok");
};
$("addDp").onclick=async()=>{
 const raw=$("newDp").value.replace(/[^0-9]/g,""),n=Number(raw);
 if(!n)return;
 if(!validateDpAddition(n))return;

 const created_at=new Date().toISOString();

 // DP baru disimpan sebagai draft di form terlebih dahulu.
 // Ini penting saat item/harga PO sedang diedit tetapi belum disimpan:
 // validasi memakai Total Pesanan yang tampil sekarang, bukan total lama di database.
 payments.push({nominal:n,created_at});
 $("newDp").value="";
 renderPayments();

 if(editing?.id){
   showToast("DP ditambahkan ke draft. Klik Simpan PO untuk menyimpan perubahan.","ok");
 }else{
   showToast("DP ditambahkan. Klik Simpan PO untuk menyimpan.","ok");
 }
};$("newDp").oninput=e=>{const n=e.target.value.replace(/[^0-9]/g,"");e.target.value=n?new Intl.NumberFormat("id-ID").format(n):""};$("payments").onclick=async e=>{
 const draftBtn=e.target.closest("[data-remove-draft]");
 if(draftBtn){
   const idx=Number(draftBtn.dataset.removeDraft);
   payments.splice(idx,1);
   renderPayments();
   showToast("Draft DP dihapus.");
   return;
 }

 const undoBtn=e.target.closest("[data-undo-pay]");
 if(undoBtn){
   const idx=Number(undoBtn.dataset.undoPay),p=payments[idx];
   if(!p?.id)return;
   if(!confirm(`Undo DP ${rp(p.nominal)}?\n\nUndo hanya tersedia maksimal 30 detik setelah input.`))return;
   try{
     await api("/api/payments/"+p.id,{
       method:"DELETE",
       headers:{"Content-Type":"application/json"},
       body:JSON.stringify({reason:"Undo 30 detik"})
     });
     showToast("DP berhasil di-undo dan tercatat di riwayat aktivitas.");
     await loadAll();
     const fresh=orders.find(x=>x.id===editing?.id);
     if(fresh){editing=fresh;payments=fresh.payments.map(x=>({...x}));renderPayments()}
   }catch(err){
     alert(err.message);
   }
   return;
 }

 const requestBtn=e.target.closest("[data-request-correction]");
 if(requestBtn){
   const idx=Number(requestBtn.dataset.requestCorrection),p=payments[idx];
   if(!p?.id)return;

   const raw=prompt(
     `AJUKAN KOREKSI DP\n\nNominal saat ini: ${rp(p.nominal)}\nMasukkan nominal yang seharusnya.\nIsi 0 jika DP ingin dibatalkan:`,
     String(Math.round(Number(p.nominal||0)))
   );
   if(raw===null)return;
   const requested=Number(String(raw).replace(/[^0-9]/g,""));
   if(!Number.isFinite(requested)||requested<0){
     alert("Nominal koreksi tidak valid.");
     return;
   }

   const reason=prompt("Alasan koreksi DP (wajib):","");
   if(reason===null)return;
   if(reason.trim().length<3){
     alert("Alasan koreksi minimal 3 karakter.");
     return;
   }

   try{
     await api("/api/payments/"+p.id+"/correction-request",{
       method:"POST",
       headers:{"Content-Type":"application/json"},
       body:JSON.stringify({requested_nominal:requested,reason:reason.trim()})
     });
     alert("Pengajuan koreksi DP berhasil dikirim ke SERVER.\n\nDP aktif belum berubah sampai SERVER menyetujui.");
   }catch(err){
     alert(err.message);
   }
   return;
 }

 const correctBtn=e.target.closest("[data-correct-pay]");
 if(correctBtn){
   const idx=Number(correctBtn.dataset.correctPay),p=payments[idx];
   if(!p?.id)return;

   const raw=prompt(
     `KOREKSI DP - SERVER\n\nNominal saat ini: ${rp(p.nominal)}\nMasukkan nominal baru.\nIsi 0 untuk membatalkan DP:`,
     String(Math.round(Number(p.nominal||0)))
   );
   if(raw===null)return;
   const nominal=Number(String(raw).replace(/[^0-9]/g,""));
   const reason=prompt("Alasan koreksi (wajib):","");
   if(reason===null)return;

   try{
     await api("/api/payments/"+p.id+"/correct",{
       method:"PUT",
       headers:{"Content-Type":"application/json"},
       body:JSON.stringify({nominal,reason:reason.trim()})
     });
     showToast("Koreksi DP berhasil dan tercatat.");
     await loadAll();
     const fresh=orders.find(x=>x.id===editing?.id);
     if(fresh){editing=fresh;payments=fresh.payments.map(x=>({...x}));renderPayments()}
   }catch(err){
     alert(err.message);
   }
   return;
 }

 const cancelBtn=e.target.closest("[data-cancel-pay]");
 if(cancelBtn){
   const idx=Number(cancelBtn.dataset.cancelPay),p=payments[idx];
   if(!p?.id)return;

   const reason=prompt(`Batalkan DP ${rp(p.nominal)}?\n\nMasukkan alasan pembatalan:`, "");
   if(reason===null)return;
   if(reason.trim().length<3){
     alert("Alasan pembatalan minimal 3 karakter.");
     return;
   }

   try{
     await api("/api/payments/"+p.id,{
       method:"DELETE",
       headers:{"Content-Type":"application/json"},
       body:JSON.stringify({reason:reason.trim()})
     });
     showToast("DP dibatalkan dan tercatat di riwayat aktivitas.");
     await loadAll();
     const fresh=orders.find(x=>x.id===editing?.id);
     if(fresh){editing=fresh;payments=fresh.payments.map(x=>({...x}));renderPayments()}
   }catch(err){
     alert(err.message);
   }
 }
};
$("estimasi").onchange=$("tanggal").onchange=calc;$("images").onchange=e=>{addPhotoFiles(e.target.files);e.target.value=""};
const dropZone=$("photoDropZone");
const browsePhotosBtn=$("browsePhotosBtn");

browsePhotosBtn.onclick=e=>{
 e.stopPropagation();
 $("images").click();
};

dropZone.onclick=e=>{
 if(e.target.closest("#browsePhotosBtn"))return;
 $("images").click();
};

dropZone.onkeydown=e=>{
 if(e.key==="Enter"||e.key===" "){
   e.preventDefault();
   $("images").click();
 }
};

["dragenter","dragover"].forEach(evt=>{
 dropZone.addEventListener(evt,e=>{
   e.preventDefault();
   e.stopPropagation();
   dropZone.classList.add("drag-over");
   $("photoDropHint").classList.add("show");
 });
});

["dragleave","drop"].forEach(evt=>{
 dropZone.addEventListener(evt,e=>{
   e.preventDefault();
   e.stopPropagation();
   dropZone.classList.remove("drag-over");
   $("photoDropHint").classList.remove("show");
 });
});

dropZone.addEventListener("drop",e=>{
 if(e.dataTransfer?.files?.length){
   addPhotoFiles(e.dataTransfer.files);
 }
});

$("gallery").onclick=async e=>{const p=e.target.closest("[data-remove-pending]");if(p){pendingFiles.splice(Number(p.dataset.removePending),1);renderGallery();return}const d=e.target.closest("[data-del-img]");if(d&&confirm("Hapus foto ini?")){await api("/api/images/"+d.dataset.delImg,{method:"DELETE"});if(editing){editing.images=editing.images.filter(x=>x.id!=d.dataset.delImg);renderGallery()}}};
$("poForm").onsubmit=async e=>{e.preventDefault();const total=currentOrderTotal(),dpTotal=currentDpTotal();if(dpTotal>total+.01){alert(`⚠️ DP MELEBIHI TOTAL HARGA\n\nPO tidak dapat disimpan karena Total DP lebih besar dari Total Pesanan.\n\nTotal Pesanan : ${rp(total)}\nTotal DP : ${rp(dpTotal)}\nSelisih Lebih : ${rp(dpTotal-total)}`);return}const payload={tanggal:$("tanggal").value,customer:$("customer").value.trim(),estimasi_hari:Number($("estimasi").value),status:$("status").value,catatan:$("catatan").value,items,payments};try{const o=editing?await api("/api/orders/"+editing.id,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}):await api("/api/orders",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});if(pendingFiles.length){const fd=new FormData();pendingFiles.forEach(f=>fd.append("images",f));await api("/api/orders/"+o.id+"/images",{method:"POST",body:fd})}$("dialog").close();await loadAll()}catch(err){alert(err.message)}};
$("search").oninput=()=>{currentPage=1;renderTable()};$("statusFilter").onchange=()=>{currentPage=1;renderTable()};$("sortNoPoBtn").onclick=()=>{noPoSort=noPoSort==="az"?"za":"az";currentPage=1;renderTable()};$("pagination").onclick=e=>{const p=e.target.closest("[data-page]"),n=e.target.closest("[data-nav]");if(p)currentPage=Number(p.dataset.page);if(n?.dataset.nav==="prev")currentPage--;if(n?.dataset.nav==="next")currentPage++;renderTable()};
if(window.APP_IS_SERVER){$("selectAllRows").onchange=e=>{document.querySelectorAll(".order-select").forEach(c=>{c.checked=e.target.checked;const id=Number(c.dataset.selectId);e.target.checked?selectedOrderIds.add(id):selectedOrderIds.delete(id)});updateBulkUI()};$("tbody").addEventListener("change",e=>{const c=e.target.closest(".order-select");if(c){const id=Number(c.dataset.selectId);c.checked?selectedOrderIds.add(id):selectedOrderIds.delete(id);updateBulkUI()}});$("deleteSelectedBtn").onclick=async()=>{const ids=[...selectedOrderIds];if(!ids.length||!confirm(`Hapus ${ids.length} PO terpilih?`))return;for(const id of ids)await api("/api/orders/"+id,{method:"DELETE"});selectedOrderIds.clear();await loadAll()};}
let actionId=null;function closeActions(){$("floatingActionMenu").hidden=true;actionId=null}function posMenu(btn){const m=$("floatingActionMenu"),r=btn.getBoundingClientRect();m.hidden=false;m.style.visibility="hidden";const mr=m.getBoundingClientRect();let left=Math.max(8,Math.min(r.right-mr.width,innerWidth-mr.width-8)),top=r.bottom+8;if(innerHeight-r.bottom<mr.height+8&&r.top>mr.height+8)top=r.top-mr.height-8;m.style.left=left+"px";m.style.top=Math.max(8,top)+"px";m.style.visibility="visible"}
document.addEventListener("click",async e=>{const t=e.target.closest("[data-action-menu]");if(t){e.stopPropagation();actionId=Number(t.dataset.actionMenu);posMenu(t);return}const a=e.target.closest("[data-floating-action]");if(a){e.stopPropagation();const id=actionId,o=orders.find(x=>x.id===id),type=a.dataset.floatingAction;closeActions();if(!o)return;if(type==="edit"){loadForm(o);$("dialog").showModal()}if(type==="wa"){const ok=await copyTextAuto(waText(o));showToast(ok?"Pesanan otomatis disalin ke clipboard.":"Browser memblokir copy otomatis.",ok?"ok":"error")}if(type==="struk")open("/print/receipt/"+id,"_blank");if(type==="dp")open("/print/dp/"+id,"_blank");if(type==="delete"&&window.APP_IS_SERVER&&confirm("Hapus PO ini?")){await api("/api/orders/"+id,{method:"DELETE"});await loadAll()}return}if(!e.target.closest("#floatingActionMenu"))closeActions()});
addEventListener("scroll",closeActions,true);addEventListener("resize",closeActions);
let lightboxPhotos=[],lightboxIndex=0,photoZoom=1;
let photoPanX=0,photoPanY=0;
let photoDragging=false,photoDragStartX=0,photoDragStartY=0,photoDragOriginX=0,photoDragOriginY=0;

function applyPhotoTransform(){
 const img=$("photoLightboxImage");
 if(!img)return;
 img.style.transform=`translate(${photoPanX}px, ${photoPanY}px) scale(${photoZoom})`;
 $("photoZoomLabel").textContent=`${Math.round(photoZoom*100)}%`;
 const stage=$("photoLightboxStage");
 if(stage){
   stage.classList.toggle("is-zoomed",photoZoom>1);
   stage.classList.toggle("is-dragging",photoDragging);
 }
}

function resetPhotoZoom(){
 photoZoom=1;
 photoPanX=0;
 photoPanY=0;
 photoDragging=false;
 applyPhotoTransform();
}

function clampPhotoPan(){
 // Batas lunak agar gambar tidak mudah "hilang" jauh dari area preview.
 const stage=$("photoLightboxStage");
 const img=$("photoLightboxImage");
 if(!stage||!img||photoZoom<=1){
   photoPanX=0;
   photoPanY=0;
   return;
 }
 const sw=stage.clientWidth, sh=stage.clientHeight;
 const iw=img.clientWidth*photoZoom, ih=img.clientHeight*photoZoom;
 const maxX=Math.max(0,(iw-sw)/2 + sw*.25);
 const maxY=Math.max(0,(ih-sh)/2 + sh*.25);
 photoPanX=Math.max(-maxX,Math.min(maxX,photoPanX));
 photoPanY=Math.max(-maxY,Math.min(maxY,photoPanY));
}

function setPhotoZoom(nextZoom, clientX=null, clientY=null){
 const stage=$("photoLightboxStage");
 const oldZoom=photoZoom;
 nextZoom=Math.max(.5,Math.min(5,Math.round(nextZoom*100)/100));

 // Zoom ke arah posisi cursor, bukan selalu ke tengah.
 if(stage && clientX!==null && clientY!==null && oldZoom!==nextZoom){
   const rect=stage.getBoundingClientRect();
   const cx=clientX-rect.left-rect.width/2;
   const cy=clientY-rect.top-rect.height/2;
   const ratio=nextZoom/oldZoom;
   photoPanX=cx-(cx-photoPanX)*ratio;
   photoPanY=cy-(cy-photoPanY)*ratio;
 }
 photoZoom=nextZoom;
 if(photoZoom<=1){
   photoPanX=0;
   photoPanY=0;
 }
 clampPhotoPan();
 applyPhotoTransform();
}

function showLightbox(t){
 const group=t.dataset.photoGroup;
 lightboxPhotos=group
   ?[...document.querySelectorAll(`.photo-preview-trigger[data-photo-group="${group}"]`)].map(x=>({src:x.dataset.photoSrc||x.src,filename:x.dataset.photoFilename||"foto-po.jpg"}))
   :[{src:t.dataset.photoSrc||t.src,filename:t.dataset.photoFilename||"foto-po.jpg"}];
 lightboxIndex=Math.max(0,lightboxPhotos.findIndex(x=>x.src===(t.dataset.photoSrc||t.src)));
 $("photoLightbox").hidden=false;
 resetPhotoZoom();
 renderLightbox();
}

function renderLightbox(){
 const img=$("photoLightboxImage");
 img.src=lightboxPhotos[lightboxIndex].src;
 img.dataset.fit="contain";
 img.style.width="auto";
 img.style.height="auto";
 img.style.maxWidth="100%";
 img.style.maxHeight="calc(94vh - 150px)";
 img.style.objectFit="contain";
 $("photoLightboxCounter").textContent=`${lightboxIndex+1} / ${lightboxPhotos.length}`;
 $("photoPrevBtn").style.display=lightboxPhotos.length>1?"grid":"none";
 $("photoNextBtn").style.display=lightboxPhotos.length>1?"grid":"none";
 resetPhotoZoom();
}

$("photoPrevBtn").onclick=()=>{
 lightboxIndex=(lightboxIndex-1+lightboxPhotos.length)%lightboxPhotos.length;
 renderLightbox();
};
$("photoNextBtn").onclick=()=>{
 lightboxIndex=(lightboxIndex+1)%lightboxPhotos.length;
 renderLightbox();
};
$("photoZoomIn").onclick=()=>setPhotoZoom(photoZoom+.25);
$("photoZoomOut").onclick=()=>setPhotoZoom(photoZoom-.25);
$("photoZoomReset").onclick=resetPhotoZoom;
$("photoDownloadBtn").onclick=async()=>{
 const photo=lightboxPhotos[lightboxIndex];
 if(!photo?.src)return;
 const safeName=(photo.filename||"foto-po.jpg").replace(/[\\/:*?"<>|]+/g,"_");
 try{
   // Blob menjaga download bekerja konsisten untuk foto upload maupun preview lokal.
   const res=await fetch(photo.src);
   if(!res.ok)throw new Error("Foto tidak dapat diambil.");
   const blob=await res.blob();
   const url=URL.createObjectURL(blob);
   const a=document.createElement("a");
   a.href=url; a.download=safeName;
   document.body.appendChild(a); a.click(); a.remove();
   setTimeout(()=>URL.revokeObjectURL(url),1500);
   if(typeof showToast==="function")showToast("Gambar berhasil didownload.","ok");
 }catch(err){
   // Fallback: browser mengunduh/membuka sumber foto langsung.
   const a=document.createElement("a");
   a.href=photo.src; a.download=safeName; a.target="_blank";
   document.body.appendChild(a); a.click(); a.remove();
 }
};

$("photoLightboxImage").onload=()=>{
 const img=$("photoLightboxImage");
 img.style.width="auto";
 img.style.height="auto";
 img.style.objectFit="contain";
 img.style.maxWidth="100%";
 img.style.maxHeight="calc(94vh - 150px)";
 resetPhotoZoom();
};

const photoStage=$("photoLightboxStage");

// Scroll wheel mouse = zoom in/out mengikuti posisi cursor.
photoStage.addEventListener("wheel",e=>{
 e.preventDefault();
 const step=e.deltaY<0?.18:-.18;
 setPhotoZoom(photoZoom+step,e.clientX,e.clientY);
},{passive:false});

// Double click = reset zoom cepat.
photoStage.addEventListener("dblclick",e=>{
 e.preventDefault();
 resetPhotoZoom();
});

// Drag dengan mouse ketika zoom > 100%.
photoStage.addEventListener("mousedown",e=>{
 if(photoZoom<=1)return;
 if(e.target.closest(".photo-lightbox-nav"))return;
 e.preventDefault();
 photoDragging=true;
 photoDragStartX=e.clientX;
 photoDragStartY=e.clientY;
 photoDragOriginX=photoPanX;
 photoDragOriginY=photoPanY;
 applyPhotoTransform();
});

window.addEventListener("mousemove",e=>{
 if(!photoDragging)return;
 photoPanX=photoDragOriginX+(e.clientX-photoDragStartX);
 photoPanY=photoDragOriginY+(e.clientY-photoDragStartY);
 clampPhotoPan();
 applyPhotoTransform();
});

window.addEventListener("mouseup",()=>{
 if(!photoDragging)return;
 photoDragging=false;
 applyPhotoTransform();
});

// Pointer/touch drag untuk layar sentuh.
let touchPanActive=false,touchStartX=0,touchStartY=0,touchOriginX=0,touchOriginY=0;
photoStage.addEventListener("touchstart",e=>{
 if(photoZoom<=1||e.touches.length!==1)return;
 const t=e.touches[0];
 touchPanActive=true;
 touchStartX=t.clientX;
 touchStartY=t.clientY;
 touchOriginX=photoPanX;
 touchOriginY=photoPanY;
},{passive:true});

photoStage.addEventListener("touchmove",e=>{
 if(!touchPanActive||e.touches.length!==1)return;
 e.preventDefault();
 const t=e.touches[0];
 photoPanX=touchOriginX+(t.clientX-touchStartX);
 photoPanY=touchOriginY+(t.clientY-touchStartY);
 clampPhotoPan();
 applyPhotoTransform();
},{passive:false});

photoStage.addEventListener("touchend",()=>{touchPanActive=false});

document.addEventListener("click",e=>{
 const t=e.target.closest(".photo-preview-trigger");
 if(t){e.stopPropagation();showLightbox(t)}
 if(e.target.closest("[data-close-lightbox]"))$("photoLightbox").hidden=true;
});

document.addEventListener("keydown",e=>{
 if($("photoLightbox").hidden)return;
 if(e.key==="Escape")$("photoLightbox").hidden=true;
 if(e.key==="+")setPhotoZoom(photoZoom+.25);
 if(e.key==="-")setPhotoZoom(photoZoom-.25);
 if(e.key==="0")resetPhotoZoom();
 if(e.key==="ArrowLeft"&&lightboxPhotos.length>1)$("photoPrevBtn").click();
 if(e.key==="ArrowRight"&&lightboxPhotos.length>1)$("photoNextBtn").click();
});
const tutorialSteps=[["Selamat datang","Dashboard menampilkan ringkasan PO dan sisa tagihan.","👋"],["Buat PO baru","Klik + Tambah PO untuk membuat pesanan.","➕"],["Catat DP","DP dapat dicatat beberapa kali. PO yang dihapus dapat direstore oleh SERVER.","💳"],["Cari & urutkan","Gunakan pencarian, status, sort No PO, dan pagination.","↕️"],["Print & laporan","Gunakan Aksi untuk print/copy dan export Excel untuk laporan.","🖨️"]];let ti=0;function tr(){const s=tutorialSteps[ti];$("tutorialTitle").textContent=s[0];$("tutorialText").textContent=s[1];$("tutorialVisual").innerHTML=`<div class="tutorial-icon">${s[2]}</div>`;$("tutorialStepLabel").textContent=`LANGKAH ${ti+1} DARI ${tutorialSteps.length}`;$("tutorialProgress").innerHTML=tutorialSteps.map((_,i)=>`<span class="${i===ti?"active":""}"></span>`).join("");$("tutorialPrev").disabled=!ti;$("tutorialNext").textContent=ti===tutorialSteps.length-1?"Selesai":"Lanjut"}function openTut(){ti=0;tr();$("tutorialOverlay").hidden=false}function closeTut(){$("tutorialOverlay").hidden=true;localStorage.setItem("poTutorialSeen","1")}$("tutorialBtn").onclick=openTut;$("tutorialSkip").onclick=$("tutorialSkipTop").onclick=closeTut;$("tutorialPrev").onclick=()=>{if(ti){ti--;tr()}};$("tutorialNext").onclick=()=>{if(ti<tutorialSteps.length-1){ti++;tr()}else closeTut()};if(!localStorage.getItem("poTutorialSeen"))setTimeout(openTut,400);loadAll();

document.addEventListener("keydown",e=>{
 if(e.key==="Escape"){
   const modal=$("historyModal");
   if(modal?.classList.contains("show")){
     modal.classList.remove("show");
     document.body.classList.remove("history-modal-open");
   }
 }
});

document.addEventListener("click",e=>{
 const h=e.target.closest("[data-history]");
 if(h){
   e.preventDefault();
   e.stopPropagation();
   showOrderHistory(Number(h.dataset.history));
   return;
 }
 const close=e.target.closest("[data-close-history]");
 if(close || e.target.id==="historyModal"){
   const modal=$("historyModal");
   if(modal)modal.classList.remove("show");
   document.body.classList.remove("history-modal-open");
 }
});