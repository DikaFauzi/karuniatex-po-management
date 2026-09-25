let orders=[],master={customers:[],fabrics:[],colors:[]},items=[],payments=[],editing=null,pendingFiles=[];
let noPoSort="az",currentPage=1;const pageSize=10,selectedOrderIds=new Set(),$=id=>document.getElementById(id);
const rp=n=>new Intl.NumberFormat("id-ID",{style:"currency",currency:"IDR",maximumFractionDigits:0}).format(Number(n||0));
const fmtDate=v=>v?new Date(v+"T00:00:00").toLocaleDateString("id-ID"):"-";
const fmtDT=v=>v?new Date(v).toLocaleString("id-ID"):"-";
const totalItem=i=>Number(i.qty_kg||0)*Number(i.harga_per_kg||0);
async function api(url,opt={}){const r=await fetch(url,opt),j=await r.json();if(!r.ok)throw new Error(j.error||"Error");return j}
function esc(v){return String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
function statusBadge(s){return `<span class="badge">${esc(s)}</span>`}
function calc(){
 const total=items.reduce((a,x)=>a+totalItem(x),0),dp=payments.reduce((a,x)=>a+Number(x.nominal||0),0);
 $("sTotal").textContent=rp(total);$("sDp").textContent=rp(dp);$("sSisa").textContent=rp(Math.max(total-dp,0));
 if($("tanggal").value){const d=new Date($("tanggal").value+"T00:00:00");d.setDate(d.getDate()+Number($("estimasi").value||21));$("sTarget").textContent=d.toLocaleDateString("id-ID")}
}
function renderItems(){
 $("items").innerHTML=items.map((i,n)=>`<article class="material-card" data-row="${n}">
 <div class="material-card-head"><div><span class="material-number">BAHAN ${n+1}</span><strong>${esc(i.jenis_kain||"Belum dipilih")}${i.warna?" • "+esc(i.warna):""}</strong></div><button type="button" class="remove-material" data-del-item="${n}">Hapus</button></div>
 <div class="material-fields">
 <label class="field"><span>Jenis Kain</span><input data-f="jenis_kain" list="fabricList" value="${esc(i.jenis_kain||"")}" placeholder="Pilih / ketik jenis kain"></label>
 <label class="field"><span>Warna</span><input data-f="warna" list="colorList" value="${esc(i.warna||"")}" placeholder="Pilih / ketik warna"></label>
 <label class="field"><span>Qty KG</span><div class="suffix-input"><input data-f="qty_kg" type="number" min="0" step=".01" value="${i.qty_kg||""}"><em>KG</em></div></label>
 <label class="field"><span>Qty Roll</span><div class="suffix-input"><input data-f="qty_roll" type="number" min="0" step=".01" value="${i.qty_roll||""}"><em>ROLL</em></div></label>
 <label class="field"><span>Harga / KG</span><div class="prefix-input"><em>Rp</em><input data-f="harga_per_kg" type="number" min="0" step="1" value="${i.harga_per_kg||""}"></div></label>
 <div class="item-total-card"><span>Total Item</span><strong>${rp(totalItem(i))}</strong><small>Qty KG × Harga/KG</small></div></div></article>`).join("");calc()
}
function renderPayments(){
 $("payments").innerHTML=payments.length?payments.map((p,n)=>`<div class="pay-row"><div><b>${rp(p.nominal)}</b><small>${fmtDT(p.created_at)}</small></div><button type="button" data-del-pay="${n}">Hapus</button></div>`).join(""):'<div class="empty-inline">Belum ada pembayaran DP.</div>';calc()
}
function renderGallery(){
 const existing=editing?.images||[];
 $("gallery").innerHTML=existing.map(x=>`<div class="photo-card"><img class="photo-preview-trigger" src="/static/uploads/${x.filename}" data-photo-src="/static/uploads/${x.filename}" data-photo-title="${esc(editing?.no_po||"Foto Bukti")}"><button type="button" data-del-img="${x.id}">×</button></div>`).join("")+
 pendingFiles.map((f,n)=>`<div class="photo-card pending"><img class="photo-preview-trigger" src="${URL.createObjectURL(f)}" data-photo-title="Foto Baru"><button type="button" data-remove-pending="${n}">×</button></div>`).join("");
 $("photoCount").textContent=`${existing.length+pendingFiles.length} foto`
}
function resetForm(){
 editing=null;items=[{jenis_kain:"",warna:"",qty_kg:0,qty_roll:0,harga_per_kg:0}];payments=[];pendingFiles=[];
 $("editId").value="";$("noPo").value="Otomatis saat disimpan";$("tanggal").value=new Date().toISOString().slice(0,10);$("customer").value="";$("estimasi").value=21;$("status").value="BELUM DP";$("catatan").value="";$("images").value="";renderItems();renderPayments();renderGallery()
}
function loadForm(o){
 editing=o;items=o.items.map(x=>({...x}));payments=o.payments.map(x=>({...x}));pendingFiles=[];
 $("editId").value=o.id;$("noPo").value=o.no_po;$("tanggal").value=o.tanggal;$("customer").value=o.customer;$("estimasi").value=o.estimasi_hari;$("status").value=o.status;$("catatan").value=o.catatan||"";$("images").value="";renderItems();renderPayments();renderGallery()
}
async function loadAll(){
 [orders,master]=await Promise.all([api("/api/orders"),api("/api/master")]);
 $("customerList").innerHTML=(master.customers||[]).map(x=>`<option value="${esc(typeof x==="object"?x.name:x)}">`).join("");
 $("fabricList").innerHTML=(master.fabrics||[]).map(x=>`<option value="${esc(typeof x==="object"?x.name:x)}">`).join("");
 $("colorList").innerHTML=(master.colors||[]).map(x=>`<option value="${esc(typeof x==="object"?x.name:x)}">`).join("");renderTable()
}
function renderPagination(total){
 const pages=Math.max(1,Math.ceil(total/pageSize));currentPage=Math.min(Math.max(1,currentPage),pages);
 const from=total?((currentPage-1)*pageSize)+1:0,to=total?Math.min(currentPage*pageSize,total):0;
 $("pageInfo").textContent=`Showing ${from} to ${to} of ${total} entries`;
 const a=[`<button class="dt-page-btn" data-nav="prev" ${currentPage===1?"disabled":""}>Previous</button>`];
 let s=Math.max(1,currentPage-2),e=Math.min(pages,s+4);if(e-s<4)s=Math.max(1,e-4);
 if(s>1){a.push('<button class="dt-page-btn" data-page="1">1</button>');if(s>2)a.push('<span class="dt-ellipsis">…</span>')}
 for(let p=s;p<=e;p++)a.push(`<button class="dt-page-btn ${p===currentPage?"active":""}" data-page="${p}">${p}</button>`);
 if(e<pages){if(e<pages-1)a.push('<span class="dt-ellipsis">…</span>');a.push(`<button class="dt-page-btn" data-page="${pages}">${pages}</button>`)}
 a.push(`<button class="dt-page-btn" data-nav="next" ${currentPage===pages?"disabled":""}>Next</button>`);$("pagination").innerHTML=a.join("")
}
function renderTable(){
 const q=$("search").value.trim().toLowerCase(),sf=$("statusFilter").value;
 let list=orders.filter(o=>(!sf||o.status===sf)&&(!q||[o.no_po,o.customer,...o.items.flatMap(i=>[i.jenis_kain,i.warna])].join(" ").toLowerCase().includes(q)));
 list.sort((a,b)=>noPoSort==="az"?String(a.no_po).localeCompare(String(b.no_po),"id",{numeric:true}):String(b.no_po).localeCompare(String(a.no_po),"id",{numeric:true}));
 const pages=Math.max(1,Math.ceil(list.length/pageSize));if(currentPage>pages)currentPage=pages;const shown=list.slice((currentPage-1)*pageSize,currentPage*pageSize);
 $("tbody").innerHTML=shown.map(o=>`<tr><td><div class="no-po-cell"><input class="row-checkbox order-select" type="checkbox" data-select-id="${o.id}" ${selectedOrderIds.has(Number(o.id))?"checked":""}><b>${esc(o.no_po)}</b></div></td>
 <td>${fmtDT(o.created_at)}</td><td>${esc(o.customer)}</td><td>${o.items.map(i=>`<div><b>${esc(i.jenis_kain)}</b> • ${esc(i.warna)}<br><small>${i.qty_kg} KG • ${i.qty_roll} Roll × ${rp(i.harga_per_kg)}</small></div>`).join("")}</td>
 <td>${o.total_qty_kg} KG</td><td>${o.total_roll} Roll</td><td><b>${rp(o.total)}</b></td><td>${rp(o.total_dp)}</td><td>${rp(o.sisa)}</td><td>${fmtDate(o.target)}</td><td>${statusBadge(o.status)}</td>
 <td>${o.images.slice(0,3).map((x,n)=>`<img class="thumb photo-preview-trigger" src="/static/uploads/${x.filename}" data-photo-src="/static/uploads/${x.filename}" data-photo-group="${o.id}" data-photo-title="${esc(o.no_po)}">`).join("")}</td>
 <td><button class="action-trigger" type="button" data-action-menu="${o.id}">Aksi</button></td></tr>`).join("");
 $("kTotal").textContent=orders.length;$("kProses").textContent=orders.filter(o=>o.status==="PROSES").length;$("kReady").textContent=orders.filter(o=>o.status==="READY").length;$("kDone").textContent=orders.filter(o=>o.status==="DONE").length;$("kSisa").textContent=rp(orders.reduce((a,o)=>a+o.sisa,0));
 renderPagination(list.length);updateBulkUI()
}
function updateBulkUI(){const n=selectedOrderIds.size;$("deleteSelectedBtn").textContent=`Hapus Dipilih (${n})`;$("deleteSelectedBtn").disabled=!n;const c=[...document.querySelectorAll(".order-select")],m=$("selectAllRows");m.checked=!!c.length&&c.every(x=>x.checked);m.indeterminate=c.some(x=>x.checked)&&!m.checked}
function waText(o){const a=[`📦 *PESANAN PO AN OTS*`,`No PO: *${o.no_po}*`,`Customer: *${o.customer}*`,`Status: *${o.status}*`,`Target: ${fmtDate(o.target)}`,"","*DETAIL PESANAN*"];o.items.forEach((i,n)=>a.push(`${n+1}. *${i.jenis_kain}* - ${i.warna}\n   ${i.qty_kg} KG • ${i.qty_roll} Roll • ${rp(i.harga_per_kg)}/KG\n   Total: *${rp(totalItem(i))}*`));a.push("",`Total: *${rp(o.total)}*`,`DP: *${rp(o.total_dp)}*`,`Sisa: *${rp(o.sisa)}*`);return a.join("\n")}
$("addBtn").onclick=()=>{resetForm();$("formTitle").textContent="Tambah PO";$("dialog").showModal()};$("closeBtn").onclick=$("cancelBtn").onclick=()=>$("dialog").close();
$("addItem").onclick=()=>{items.push({jenis_kain:"",warna:"",qty_kg:0,qty_roll:0,harga_per_kg:0});renderItems()};
$("items").addEventListener("input",e=>{const row=e.target.closest("[data-row]");if(!row||!e.target.dataset.f)return;const n=Number(row.dataset.row),f=e.target.dataset.f,v=e.target.value;items[n][f]=["qty_kg","qty_roll","harga_per_kg"].includes(f)?Number(v||0):v;if(f==="qty_kg")items[n].qty_roll=Number((items[n].qty_kg/25).toFixed(2));if(f==="qty_roll")items[n].qty_kg=Number((items[n].qty_roll*25).toFixed(2));renderItems()});
$("items").addEventListener("click",e=>{const b=e.target.closest("[data-del-item]");if(b){items.splice(Number(b.dataset.delItem),1);if(!items.length)items.push({jenis_kain:"",warna:"",qty_kg:0,qty_roll:0,harga_per_kg:0});renderItems()}});
$("addDp").onclick=()=>{const raw=$("newDp").value.replace(/[^0-9]/g,""),n=Number(raw);if(!n)return;payments.push({nominal:n,created_at:new Date().toISOString()});$("newDp").value="";renderPayments()};
$("newDp").addEventListener("input",e=>{const n=e.target.value.replace(/[^0-9]/g,"");e.target.value=n?new Intl.NumberFormat("id-ID").format(n):""});
$("payments").addEventListener("click",e=>{const b=e.target.closest("[data-del-pay]");if(b){payments.splice(Number(b.dataset.delPay),1);renderPayments()}});
$("estimasi").onchange=$("tanggal").onchange=calc;
$("images").addEventListener("change",e=>{pendingFiles=[...pendingFiles,...e.target.files];e.target.value="";renderGallery()});
$("gallery").addEventListener("click",async e=>{const p=e.target.closest("[data-remove-pending]");if(p){pendingFiles.splice(Number(p.dataset.removePending),1);renderGallery();return}const d=e.target.closest("[data-del-img]");if(d&&confirm("Hapus foto ini?")){await api("/api/images/"+d.dataset.delImg,{method:"DELETE"});if(editing){editing.images=editing.images.filter(x=>x.id!=d.dataset.delImg);renderGallery()}}});
$("poForm").onsubmit=async e=>{e.preventDefault();const payload={tanggal:$("tanggal").value,customer:$("customer").value.trim(),estimasi_hari:Number($("estimasi").value),status:$("status").value,catatan:$("catatan").value,items,payments};try{const o=editing?await api("/api/orders/"+editing.id,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}):await api("/api/orders",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});if(pendingFiles.length){const fd=new FormData();pendingFiles.forEach(f=>fd.append("images",f));await api("/api/orders/"+o.id+"/images",{method:"POST",body:fd})}$("dialog").close();await loadAll()}catch(err){alert(err.message)}};
$("search").oninput=()=>{currentPage=1;renderTable()};$("statusFilter").onchange=()=>{currentPage=1;renderTable()};
$("sortNoPoBtn").onclick=()=>{noPoSort=noPoSort==="az"?"za":"az";$("sortNoPoBtn").dataset.sort=noPoSort;currentPage=1;renderTable()};
$("pagination").onclick=e=>{const p=e.target.closest("[data-page]"),n=e.target.closest("[data-nav]");if(p)currentPage=Number(p.dataset.page);if(n?.dataset.nav==="prev")currentPage--;if(n?.dataset.nav==="next")currentPage++;renderTable()};
$("selectAllRows").onchange=e=>{document.querySelectorAll(".order-select").forEach(c=>{c.checked=e.target.checked;const id=Number(c.dataset.selectId);e.target.checked?selectedOrderIds.add(id):selectedOrderIds.delete(id)});updateBulkUI()};
$("tbody").addEventListener("change",e=>{const c=e.target.closest(".order-select");if(c){const id=Number(c.dataset.selectId);c.checked?selectedOrderIds.add(id):selectedOrderIds.delete(id);updateBulkUI()}});
$("deleteSelectedBtn").onclick=async()=>{const ids=[...selectedOrderIds];if(!ids.length||!confirm(`Hapus ${ids.length} PO terpilih?`))return;for(const id of ids)await api("/api/orders/"+id,{method:"DELETE"});selectedOrderIds.clear();await loadAll()};
$("excelImport").onchange=async e=>{if(!e.target.files[0])return;const fd=new FormData();fd.append("file",e.target.files[0]);try{const j=await api("/excel/import",{method:"POST",body:fd});alert(`Import berhasil: ${j.imported} PO`);await loadAll()}catch(err){alert(err.message)}e.target.value=""};
let actionId=null;function closeActions(){$("floatingActionMenu").hidden=true;actionId=null}
function posMenu(btn){const m=$("floatingActionMenu"),r=btn.getBoundingClientRect();m.hidden=false;m.style.visibility="hidden";const mr=m.getBoundingClientRect();let left=Math.max(8,Math.min(r.right-mr.width,innerWidth-mr.width-8)),top=r.bottom+8;if(innerHeight-r.bottom<mr.height+8&&r.top>mr.height+8)top=r.top-mr.height-8;m.style.left=left+"px";m.style.top=Math.max(8,top)+"px";m.style.visibility="visible"}
document.addEventListener("click",async e=>{const t=e.target.closest("[data-action-menu]");if(t){e.stopPropagation();const id=Number(t.dataset.actionMenu);if(actionId===id&&!$("floatingActionMenu").hidden){closeActions();return}actionId=id;posMenu(t);return}const a=e.target.closest("[data-floating-action]");if(a){e.stopPropagation();const id=actionId,o=orders.find(x=>x.id===id),type=a.dataset.floatingAction;closeActions();if(!o)return;if(type==="edit"){loadForm(o);$("formTitle").textContent="Edit PO";$("dialog").showModal()}if(type==="wa"){try{await navigator.clipboard.writeText(waText(o));alert("Pesanan berhasil disalin.")}catch{prompt("Salin teks berikut:",waText(o))}}if(type==="struk")open("/print/receipt/"+id,"_blank");if(type==="dp")open("/print/dp/"+id,"_blank");if(type==="delete"&&confirm("Hapus PO ini?")){await api("/api/orders/"+id,{method:"DELETE"});await loadAll()}return}if(!e.target.closest("#floatingActionMenu"))closeActions()});
addEventListener("scroll",closeActions,true);addEventListener("resize",closeActions);
let lightboxPhotos=[],lightboxIndex=0;function showLightbox(t){const group=t.dataset.photoGroup;lightboxPhotos=group?[...document.querySelectorAll(`.photo-preview-trigger[data-photo-group="${group}"]`)].map(x=>x.dataset.photoSrc||x.src):[{src:t.dataset.photoSrc||t.src}];lightboxIndex=Math.max(0,lightboxPhotos.findIndex(x=>x.src===(t.dataset.photoSrc||t.src)));$("photoLightbox").hidden=false;renderLightbox()}
function renderLightbox(){if(!lightboxPhotos.length)return;$("photoLightboxImage").src=lightboxPhotos[lightboxIndex].src;$("photoLightboxCounter").textContent=`${lightboxIndex+1} / ${lightboxPhotos.length}`;$("photoPrevBtn").hidden=$("photoNextBtn").hidden=lightboxPhotos.length<2}
function closeLightbox(){$("photoLightbox").hidden=true;$("photoLightboxImage").src=""}
document.addEventListener("click",e=>{const t=e.target.closest(".photo-preview-trigger");if(t){e.stopPropagation();showLightbox(t)}if(e.target.closest("[data-close-lightbox]"))closeLightbox()});
$("photoPrevBtn").onclick=()=>{lightboxIndex=(lightboxIndex-1+lightboxPhotos.length)%lightboxPhotos.length;renderLightbox()};$("photoNextBtn").onclick=()=>{lightboxIndex=(lightboxIndex+1)%lightboxPhotos.length;renderLightbox()};
const tutorialSteps=[["Selamat datang","Dashboard menampilkan ringkasan PO dan sisa tagihan.","👋"],["Buat PO baru","Klik + Tambah PO untuk membuat pesanan multi bahan dan warna.","➕"],["Catat DP","Pembayaran DP dapat dicatat beberapa kali dengan tanggal dan jam.","💳"],["Cari & urutkan","Gunakan pencarian, status, sort No PO, dan pagination.","↕️"],["Print & laporan","Gunakan menu Aksi untuk print/copy dan export Excel untuk laporan.","🖨️"]];let ti=0;
function tutorialRender(){const s=tutorialSteps[ti];$("tutorialTitle").textContent=s[0];$("tutorialText").textContent=s[1];$("tutorialVisual").innerHTML=`<div class="tutorial-icon">${s[2]}</div>`;$("tutorialStepLabel").textContent=`LANGKAH ${ti+1} DARI ${tutorialSteps.length}`;$("tutorialProgress").innerHTML=tutorialSteps.map((_,i)=>`<span class="${i===ti?"active":""}"></span>`).join("");$("tutorialPrev").disabled=!ti;$("tutorialNext").textContent=ti===tutorialSteps.length-1?"Selesai":"Lanjut"}
function openTutorial(){ti=0;tutorialRender();$("tutorialOverlay").hidden=false}function closeTutorial(){$("tutorialOverlay").hidden=true;localStorage.setItem("poTutorialSeen","1")}
$("tutorialBtn").onclick=openTutorial;$("tutorialSkip").onclick=$("tutorialSkipTop").onclick=closeTutorial;$("tutorialPrev").onclick=()=>{if(ti){ti--;tutorialRender()}};$("tutorialNext").onclick=()=>{if(ti<tutorialSteps.length-1){ti++;tutorialRender()}else closeTutorial()};
if(!localStorage.getItem("poTutorialSeen"))setTimeout(openTutorial,400);loadAll();
