/* Grapevine hero art — animated vineyard with string lights.
   Original artwork code by the NETA 65 GV/LV committee web servant, carried over
   unchanged from the previous site: everything between the "ARTWORK" and "SETUP"
   markers below is the original drawing code, line for line — the one exception is
   that `reducedMotion` is a `let` (not a `const`), so the setup code can follow the
   OS setting when it changes while the page is open.

   SETUP (generalized for the shared page hero, ui.pageHero in macros/ui.njk):
   - layouts/base.njk loads this file (defer) on EVERY page — pages need no pageScripts entry.
   - Every hero gets its own art: <section data-gv-hero> (the macro), plus the legacy
     .page-hero class and the old home hero (#homeHero) until those are migrated. The art
     draws into the hero's <canvas class="gv-hero-canvas"> (or #grapevineCanvas); a hero
     without a canvas gets one added as its first child.
   - The canvas follows the hero's size (ResizeObserver + window resize: rotation, zoom,
     web fonts arriving, Alpine filling in text). The vineyard is re-planted only when the
     width changes or the height changes a lot, so small reflows never make it jump.
   - The animation pauses while the hero is scrolled out of view (IntersectionObserver) and
     while the tab is hidden, and picks up again when it comes back.
   - prefers-reduced-motion (or "Reduce" motion in the site's Reading & display panel,
     <html data-motion="reduce">): no animation loop — one still picture is painted, and
     repainted at the right size on every resize. Changing either while the page is open works.
     Data saver (<html data-saver="on">, also while offline) gets the same still picture.
   - Battery: the lights run for 25 s, then settle into the still picture; moving the mouse over
     the hero (or touching it) brings them back for 20 s. Phones and tablets (a coarse pointer) and
     low-power computers (4 cores or fewer) start with the still picture — a touch wakes it.
   - Loading the file twice, or on a page without a hero, is harmless (it does nothing).
   Optional API: window.GVHeroArt.init(root) sets up heroes added later; GVHeroArt.pause()
   and .resume(); window.GV_CANVAS.start()/.stop() are kept for older callers. */
(function(){
  if(window.GVHeroArt)return; // already running on this page
  const RM=window.matchMedia?window.matchMedia("(prefers-reduced-motion: reduce)"):{matches:false};
  // The OS setting, or "Reduce" in the site's Reading & display panel (<html data-motion="reduce">).
  function motionReduced(){return RM.matches||document.documentElement.getAttribute("data-motion")==="reduce"}
  // Data saver (<html data-saver="on">: the Aa panel's switch, or offline — pwa.js) counts too: the
  // still picture, no animation loop (it spends battery and processor on the phones that most need them).
  function stillArt(){return motionReduced()||document.documentElement.getAttribute("data-saver")==="on"}

  function createGrapevineArt(hero,canvasEl){
  // ======================= ARTWORK (original, unchanged) =======================
  let cvs,ctx,W,H,raf=null,scene=null,mouseX=-1,mouseY=-1;
  // Offscreen canvas for static elements (canes, branches, leaves, clusters, tendrils)
  let staticCvs=null,staticCtx=null,staticDirty=true;
  let reducedMotion=RM.matches; // SETUP: kept live, see setReducedMotion() below
  const PI=Math.PI, TAU=PI*2;
  // Throttle: target ~18fps (every ~55ms) instead of 60fps
  const FRAME_INTERVAL=55;
  let lastDrawTime=0;
  let tabVisible=true;
  function rnd(a,b){return Math.random()*(b-a)+a}
  function pick(arr){return arr[Math.floor(Math.random()*arr.length)]}
  function lerp(a,b,t){return a+(b-a)*t}
  function dist(x1,y1,x2,y2){const dx=x1-x2,dy=y1-y2;return Math.sqrt(dx*dx+dy*dy)}
  function bezPt(p0,p1,p2,p3,t){
    const u=1-t;
    return{x:u*u*u*p0.x+3*u*u*t*p1.x+3*u*t*t*p2.x+t*t*t*p3.x,
           y:u*u*u*p0.y+3*u*u*t*p1.y+3*u*t*t*p2.y+t*t*t*p3.y}
  }
  const LCOL=[
    {r:255,g:220,b:90},{r:255,g:190,b:60},{r:255,g:160,b:50},
    {r:190,g:140,b:255},{r:160,g:110,b:240},
    {r:110,g:210,b:255},{r:80,g:180,b:240},
    {r:255,g:150,b:190},{r:255,g:120,b:160},
    {r:130,g:235,b:180},{r:100,g:220,b:160},
    {r:255,g:200,b:120},{r:220,g:180,b:255},{r:120,g:255,b:230},
  ];
  // ── Draw cane to a given context — uses pre-computed knots ──
  function drawCane(c,baseW,opacity,knots){
    const seg=c,steps=30; // reduced from 60
    for(let pass=0;pass<2;pass++){ // reduced from 3 passes to 2
      const w=baseW*(pass===0?1:0.5);
      const a=opacity*(pass===0?1:0.45);
      staticCtx.strokeStyle=pass===0?`rgba(170,140,105,${a})`:`rgba(130,100,75,${a})`;
      staticCtx.lineWidth=w;
      staticCtx.lineCap="round";staticCtx.lineJoin="round";
      staticCtx.beginPath();
      for(let i=0;i<=steps;i++){
        const t=i/steps;
        const pt=bezPt(seg.p0,seg.p1,seg.p2,seg.p3,t);
        const wobble=pass>0?Math.sin(t*12+seg.p0.x*0.03)*w*0.3:0;
        if(i===0)staticCtx.moveTo(pt.x+wobble,pt.y+wobble);
        else staticCtx.lineTo(pt.x+wobble,pt.y+wobble);
      }
      staticCtx.stroke();
    }
    // Pre-computed bark knots
    if(knots){knots.forEach(k=>{
      const kp=bezPt(seg.p0,seg.p1,seg.p2,seg.p3,k.t);
      staticCtx.fillStyle=`rgba(110,80,55,${opacity*0.5})`;
      staticCtx.beginPath();
      staticCtx.ellipse(kp.x,kp.y,k.rx,k.ry,k.rot,0,TAU);
      staticCtx.fill();
    })}
  }
  // ── Draw tendril to static canvas — pre-computed spiral points ──
  function drawTendrilStatic(tr){
    staticCtx.strokeStyle=`rgba(150,190,110,${tr.opacity*0.7})`;
    staticCtx.lineWidth=2;
    staticCtx.lineCap="round";
    staticCtx.beginPath();
    staticCtx.moveTo(tr.x,tr.y);
    const stemEndX=tr.x+Math.cos(tr.angle)*tr.length*0.3;
    const stemEndY=tr.y+Math.sin(tr.angle)*tr.length*0.3;
    staticCtx.lineTo(stemEndX,stemEndY);
    // Use pre-computed spiral points
    tr.spiralPts.forEach(p=>staticCtx.lineTo(p.x,p.y));
    staticCtx.stroke();
    if(tr.hasSecond){
      staticCtx.strokeStyle=`rgba(150,190,110,${tr.opacity*0.45})`;
      staticCtx.lineWidth=1.2;
      staticCtx.beginPath();
      staticCtx.moveTo(stemEndX,stemEndY);
      tr.secondPts.forEach(p=>staticCtx.lineTo(p.x,p.y));
      staticCtx.stroke();
    }
  }
  // ── Draw grape leaf to static canvas — no per-frame gradients ──
  function drawGrapeLeafStatic(lf){
    const s=lf.size,o=lf.opacity;
    staticCtx.save();staticCtx.translate(lf.x,lf.y);staticCtx.rotate(lf.angle);
    // Solid fill instead of per-frame gradient — huge perf win
    staticCtx.fillStyle=`rgba(55,140,55,${o*0.45})`;
    staticCtx.beginPath();
    staticCtx.moveTo(0,s*0.4);
    staticCtx.bezierCurveTo(s*0.05,s*0.2,s*0.15,s*0.1,s*0.35,s*0.15);
    staticCtx.bezierCurveTo(s*0.45,s*0.05,s*0.3,-s*0.05,s*0.25,-s*0.1);
    staticCtx.bezierCurveTo(s*0.5,-s*0.15,s*0.65,-s*0.3,s*0.55,-s*0.5);
    staticCtx.bezierCurveTo(s*0.45,-s*0.45,s*0.35,-s*0.35,s*0.2,-s*0.4);
    staticCtx.bezierCurveTo(s*0.25,-s*0.6,s*0.2,-s*0.8,s*0.1,-s*0.9);
    staticCtx.bezierCurveTo(s*0.05,-s*1.0,0,-s*1.05,-s*0.05,-s*1.0);
    staticCtx.bezierCurveTo(-s*0.1,-s*0.9,-s*0.15,-s*0.8,-s*0.1,-s*0.7);
    staticCtx.bezierCurveTo(-s*0.2,-s*0.8,-s*0.25,-s*0.6,-s*0.2,-s*0.4);
    staticCtx.bezierCurveTo(-s*0.35,-s*0.35,-s*0.45,-s*0.45,-s*0.55,-s*0.5);
    staticCtx.bezierCurveTo(-s*0.65,-s*0.3,-s*0.5,-s*0.15,-s*0.25,-s*0.1);
    staticCtx.bezierCurveTo(-s*0.3,-s*0.05,-s*0.45,s*0.05,-s*0.35,s*0.15);
    staticCtx.bezierCurveTo(-s*0.15,s*0.1,-s*0.05,s*0.2,0,s*0.4);
    staticCtx.closePath();
    staticCtx.fill();
    staticCtx.strokeStyle=`rgba(60,130,50,${o*0.35})`;
    staticCtx.lineWidth=1;
    staticCtx.stroke();
    // Main veins only — skip secondary random veins
    staticCtx.strokeStyle=`rgba(90,160,70,${o*0.4})`;
    staticCtx.lineWidth=1.4;
    staticCtx.beginPath();staticCtx.moveTo(0,s*0.3);staticCtx.lineTo(0,-s*0.85);staticCtx.stroke();
    staticCtx.beginPath();staticCtx.moveTo(0,s*0.1);staticCtx.quadraticCurveTo(s*0.15,-s*0.2,s*0.45,-s*0.4);staticCtx.stroke();
    staticCtx.beginPath();staticCtx.moveTo(0,s*0.1);staticCtx.quadraticCurveTo(-s*0.15,-s*0.2,-s*0.45,-s*0.4);staticCtx.stroke();
    staticCtx.beginPath();staticCtx.moveTo(0,s*0.2);staticCtx.quadraticCurveTo(s*0.12,s*0.05,s*0.3,s*0.1);staticCtx.stroke();
    staticCtx.beginPath();staticCtx.moveTo(0,s*0.2);staticCtx.quadraticCurveTo(-s*0.12,s*0.05,-s*0.3,s*0.1);staticCtx.stroke();
    // Pre-computed secondary veins
    staticCtx.strokeStyle=`rgba(90,160,70,${o*0.2})`;
    staticCtx.lineWidth=0.8;
    lf.veins.forEach(v=>{
      staticCtx.beginPath();staticCtx.moveTo(v.x1,v.y1);staticCtx.lineTo(v.x2,v.y2);staticCtx.stroke();
    });
    staticCtx.restore();
  }
  // ── Draw grape cluster to static canvas — solid fills, no per-grape gradients ──
  function drawGrapeClusterStatic(cl){
    staticCtx.save();staticCtx.translate(cl.x,cl.y);
    staticCtx.strokeStyle=`rgba(130,170,85,${cl.opacity*0.7})`;
    staticCtx.lineWidth=2.5;
    staticCtx.beginPath();staticCtx.moveTo(0,-cl.size*0.3);staticCtx.lineTo(0,0);staticCtx.stroke();
    // Use pre-computed grapes with solid fills instead of per-grape gradients
    cl.grapes.forEach(g=>{
      const o=cl.opacity;
      staticCtx.fillStyle=`hsla(${cl.hue},50%,15%,${o*0.3})`;
      staticCtx.beginPath();staticCtx.arc(g.x+1.5,g.y+1.5,g.r*1.05,0,TAU);staticCtx.fill();
      staticCtx.fillStyle=`hsla(${cl.hue},65%,48%,${o*0.75})`;
      staticCtx.beginPath();staticCtx.arc(g.x,g.y,g.r,0,TAU);staticCtx.fill();
      staticCtx.fillStyle=`hsla(${cl.hue},75%,88%,${o*0.35})`;
      staticCtx.beginPath();staticCtx.arc(g.x-g.r*0.25,g.y-g.r*0.3,g.r*0.35,0,TAU);staticCtx.fill();
    });
    staticCtx.restore();
  }
  function makeSegBetween(x0,y0,x1,y1,curvature){
    const dx=x1-x0,dy=y1-y0;
    const nx=-dy,ny=dx;
    const len=Math.sqrt(nx*nx+ny*ny)||1;
    const off=curvature/len;
    return{p0:{x:x0,y:y0},
           p1:{x:x0+dx*0.3+nx*off*rnd(0.3,0.7),y:y0+dy*0.3+ny*off*rnd(0.3,0.7)},
           p2:{x:x0+dx*0.7+nx*off*rnd(0.3,0.7),y:y0+dy*0.7+ny*off*rnd(0.3,0.7)},
           p3:{x:x1,y:y1}};
  }
  // ── Build scene — pre-compute ALL random values at build time ──
  function buildScene(){
    const canes=[],branches=[],tendrils=[],leaves=[],clusters=[],stringLights=[],particles=[];
    const isMobile=W<640,isTablet=W<1024;
    const scale=isMobile?0.65:isTablet?0.8:1;
    const cordonCount=isMobile?2:isTablet?3:3;
    const cordonYs=[];
    for(let i=0;i<cordonCount;i++){
      cordonYs.push(H*(0.2+0.55*(i/(Math.max(cordonCount-1,1))))+rnd(-H*0.05,H*0.05));
    }
    cordonYs.forEach((baseY,ci)=>{
      const fromLeft=ci%2===0;
      const startX=fromLeft?rnd(-80,-20):W+rnd(20,80);
      const endX=fromLeft?W+rnd(20,80):rnd(-80,-20);
      const segCount=isMobile?2:3;
      const segW=(endX-startX)/segCount;
      let cx=startX,cy=baseY;
      for(let s=0;s<segCount;s++){
        const nx=cx+segW;
        const ny=baseY+Math.sin((s+0.5)/segCount*PI)*rnd(-15,20)+rnd(-10,10);
        const curvature=rnd(-25,25);
        const seg=makeSegBetween(cx,cy,nx,ny,curvature);
        const thickness=rnd(6,10)*(1-s*0.08)*scale;
        // Pre-compute bark knots
        const knots=[];
        const knotCount=Math.floor(rnd(2,5));
        for(let k=0;k<knotCount;k++){
          knots.push({t:rnd(0.15,0.85),rx:thickness*rnd(0.6,1.1),ry:thickness*rnd(0.36,0.66),rot:rnd(0,PI)});
        }
        canes.push({seg,width:thickness,opacity:rnd(0.45,0.7),knots});
        const shootCount=isMobile?Math.floor(rnd(1,3)):Math.floor(rnd(2,4));
        for(let sh=0;sh<shootCount;sh++){
          const st=rnd(0.12,0.88);
          const sp=bezPt(seg.p0,seg.p1,seg.p2,seg.p3,st);
          const shootAngle=-PI/2+rnd(-0.5,0.5);
          const shootLen=rnd(50,140)*scale;
          const shootEnd={x:sp.x+Math.cos(shootAngle)*shootLen,y:sp.y+Math.sin(shootAngle)*shootLen};
          const shootSeg=makeSegBetween(sp.x,sp.y,shootEnd.x,shootEnd.y,rnd(-20,20));
          const shootW=thickness*rnd(0.35,0.55);
          const shootKnots=[];
          const skc=Math.floor(rnd(1,3));
          for(let k=0;k<skc;k++){shootKnots.push({t:rnd(0.15,0.85),rx:shootW*rnd(0.6,1.1),ry:shootW*rnd(0.36,0.66),rot:rnd(0,PI)})}
          branches.push({seg:shootSeg,width:shootW,opacity:rnd(0.35,0.55),knots:shootKnots});
          if(!isMobile&&Math.random()>0.35){
            const lt=rnd(0.35,0.75);
            const lp=bezPt(shootSeg.p0,shootSeg.p1,shootSeg.p2,shootSeg.p3,lt);
            const latAngle=shootAngle+rnd(0.4,1.2)*(Math.random()>0.5?1:-1);
            const latLen=shootLen*rnd(0.3,0.55);
            const latEnd={x:lp.x+Math.cos(latAngle)*latLen,y:lp.y+Math.sin(latAngle)*latLen};
            const latSeg=makeSegBetween(lp.x,lp.y,latEnd.x,latEnd.y,rnd(-12,12));
            branches.push({seg:latSeg,width:shootW*rnd(0.4,0.6),opacity:rnd(0.3,0.45),knots:[]});
            leaves.push({x:latEnd.x,y:latEnd.y,size:rnd(14,28)*scale,angle:latAngle+rnd(-0.5,0.5),opacity:rnd(0.5,0.75),veins:precomputeVeins(rnd(14,28)*scale)});
            if(Math.random()>0.4){
              tendrils.push(buildTendril(latEnd.x,latEnd.y,latAngle+rnd(-0.8,0.8),rnd(30,65)*scale,Math.random()>0.5?1:-1,rnd(0.4,0.6)));
            }
          }
          leaves.push({x:shootEnd.x+rnd(-5,5),y:shootEnd.y+rnd(-5,5),size:rnd(18,36)*scale,angle:shootAngle+rnd(-0.8,0.8),opacity:rnd(0.5,0.8),veins:precomputeVeins(rnd(18,36)*scale)});
          const shootLeaves=Math.floor(rnd(1,3));
          for(let sl=0;sl<shootLeaves;sl++){
            const slt=rnd(0.25,0.7);
            const slp=bezPt(shootSeg.p0,shootSeg.p1,shootSeg.p2,shootSeg.p3,slt);
            const side=Math.random()>0.5?1:-1;
            const sz=rnd(14,30)*scale;
            leaves.push({x:slp.x+side*rnd(6,14),y:slp.y+rnd(-4,4),size:sz,angle:shootAngle+side*rnd(0.6,1.4),opacity:rnd(0.45,0.7),veins:precomputeVeins(sz)});
          }
          if(Math.random()>0.3){
            const tt=rnd(0.4,0.8);
            const tp=bezPt(shootSeg.p0,shootSeg.p1,shootSeg.p2,shootSeg.p3,tt);
            const tSide=Math.random()>0.5?1:-1;
            tendrils.push(buildTendril(tp.x,tp.y,shootAngle+tSide*rnd(0.5,1.2),rnd(35,80)*scale,tSide,rnd(0.4,0.65)));
          }
          if(Math.random()<0.55){
            const gct=rnd(0.15,0.5);
            const gcp=bezPt(shootSeg.p0,shootSeg.p1,shootSeg.p2,shootSeg.p3,gct);
            clusters.push(buildCluster(gcp.x+rnd(-5,5),gcp.y+rnd(8,20),rnd(13,24)*scale,pick([255,265,275,285,295,305]),rnd(0.6,0.85)));
          }
          const sLights=Math.floor(rnd(2,5));
          for(let sli=0;sli<sLights;sli++){
            const slt=rnd(0.1,0.9);
            const slp=bezPt(shootSeg.p0,shootSeg.p1,shootSeg.p2,shootSeg.p3,slt);
            stringLights.push({x:slp.x+rnd(-3,3),y:slp.y+rnd(3,10),baseR:rnd(3.5,7),glowR:rnd(25,50),col:pick(LCOL),phase:rnd(0,TAU),speed:rnd(0.6,2.2),brightness:rnd(0.6,1),wx:slp.x,wy:slp.y});
          }
        }
        const cTendrils=Math.floor(rnd(1,3));
        for(let ct=0;ct<cTendrils;ct++){
          const tt=rnd(0.1,0.9);
          const tp=bezPt(seg.p0,seg.p1,seg.p2,seg.p3,tt);
          const tAngle=PI/2+rnd(-0.8,0.8);
          tendrils.push(buildTendril(tp.x,tp.y,tAngle,rnd(30,70)*scale,Math.random()>0.5?1:-1,rnd(0.4,0.6)));
        }
        if(Math.random()<0.4){
          const gt=rnd(0.2,0.8);
          const gp=bezPt(seg.p0,seg.p1,seg.p2,seg.p3,gt);
          clusters.push(buildCluster(gp.x,gp.y+rnd(12,28),rnd(14,24)*scale,pick([260,275,290,305]),rnd(0.6,0.85)));
        }
        const cLights=Math.floor(rnd(4,8));
        for(let cli=0;cli<cLights;cli++){
          const lt=rnd(0.05,0.95);
          const lp=bezPt(seg.p0,seg.p1,seg.p2,seg.p3,lt);
          const droop=rnd(4,12);
          stringLights.push({x:lp.x,y:lp.y+droop,baseR:rnd(4.5,8.5),glowR:rnd(32,65),col:pick(LCOL),phase:rnd(0,TAU),speed:rnd(0.6,2.2),brightness:rnd(0.6,1),wx:lp.x,wy:lp.y});
        }
        cx=nx;cy=ny;
      }
    });
    const pCount=isMobile?8:isTablet?14:20; // reduced particle counts
    for(let i=0;i<pCount;i++){
      particles.push({x:rnd(0,W),y:rnd(0,H),vx:rnd(-0.25,0.25),vy:rnd(-0.4,-0.08),r:rnd(2,4.5),col:pick(LCOL),phase:rnd(0,TAU),speed:rnd(0.5,1.5),life:rnd(0,1)});
    }
    scene={canes,branches,tendrils,leaves,clusters,stringLights,particles};
    staticDirty=true;
  }
  // Pre-compute tendril spiral points at build time
  function buildTendril(x,y,angle,length,curlDir,opacity){
    const spirals=rnd(2.5,4);
    const steps=30; // reduced from 50
    const curlSpeed=spirals*TAU/steps;
    const stemLen=length*0.3;
    const stemEndX=x+Math.cos(angle)*stemLen;
    const stemEndY=y+Math.sin(angle)*stemLen;
    let cx2=stemEndX,cy2=stemEndY,curAngle=angle,radius=length*0.2;
    const spiralPts=[];
    for(let i=0;i<steps;i++){
      curAngle+=curlDir*curlSpeed;radius*=0.97;
      cx2+=Math.cos(curAngle)*radius*0.12;cy2+=Math.sin(curAngle)*radius*0.12;
      spiralPts.push({x:cx2,y:cy2});
    }
    const hasSecond=Math.random()>0.5;
    const secondPts=[];
    if(hasSecond){
      let sx=stemEndX,sy=stemEndY,r2=length*0.13,a2=angle+PI*0.3*curlDir;
      for(let i=0;i<20;i++){
        a2-=curlDir*curlSpeed*1.2;r2*=0.96;
        sx+=Math.cos(a2)*r2*0.1;sy+=Math.sin(a2)*r2*0.1;
        secondPts.push({x:sx,y:sy});
      }
    }
    return{x,y,angle,length,opacity,spiralPts,hasSecond,secondPts};
  }
  // Pre-compute secondary leaf veins
  function precomputeVeins(size){
    const veins=[];
    for(let v=0;v<5;v++){
      const vt=rnd(0.2,0.6),vAngle=rnd(-1.2,1.2),vLen=size*rnd(0.15,0.3);
      const vx=0,vy=size*0.3-size*vt*1.1;
      veins.push({x1:vx,y1:vy,x2:vx+Math.cos(vAngle)*vLen,y2:vy+Math.sin(vAngle)*vLen});
    }
    return veins;
  }
  // Pre-compute grape positions in a cluster
  function buildCluster(x,y,size,hue,opacity){
    const rows=Math.max(4,Math.floor(size*0.55));
    const grapeR=size*0.2;
    const grapes=[];
    for(let row=0;row<rows;row++){
      const rowT=row/(rows-1);
      const count=Math.max(1,Math.round(lerp(rows,1,rowT*rowT)));
      const rowY=row*grapeR*1.7;
      const rowW=(count-1)*grapeR*1.8;
      for(let i=0;i<count;i++){
        grapes.push({x:-rowW/2+i*grapeR*1.8+rnd(-grapeR*0.2,grapeR*0.2),y:rowY+rnd(-grapeR*0.15,grapeR*0.15),r:grapeR+rnd(-0.5,0.5)});
      }
    }
    return{x,y,size,hue,opacity,grapes};
  }
  // ── Render all static elements to offscreen canvas (once per resize) ──
  function renderStaticLayer(){
    if(!staticCvs){staticCvs=document.createElement("canvas")}
    const dpr=Math.min(window.devicePixelRatio||1,2);
    staticCvs.width=W*dpr;staticCvs.height=H*dpr;
    staticCtx=staticCvs.getContext("2d");
    staticCtx.setTransform(dpr,0,0,dpr,0,0);
    if(!scene)return;
    scene.canes.forEach(c=>drawCane(c.seg,c.width,c.opacity,c.knots));
    scene.branches.forEach(b=>drawCane(b.seg,b.width,b.opacity,b.knots));
    scene.tendrils.forEach(tr=>drawTendrilStatic(tr));
    scene.leaves.forEach(lf=>drawGrapeLeafStatic(lf));
    scene.clusters.forEach(cl=>drawGrapeClusterStatic(cl));
    // Also draw light wires (static)
    scene.stringLights.forEach(l=>{
      staticCtx.strokeStyle="rgba(200,200,200,0.15)";
      staticCtx.lineWidth=0.8;
      staticCtx.beginPath();staticCtx.moveTo(l.wx,l.wy);staticCtx.lineTo(l.x,l.y);staticCtx.stroke();
    });
    staticDirty=false;
  }
  // ── Main draw loop — only animates lights + particles over static layer ──
  function draw(time){
    if(!tabVisible){raf=requestAnimationFrame(draw);return}
    // Throttle frame rate
    if(time-lastDrawTime<FRAME_INTERVAL){raf=requestAnimationFrame(draw);return}
    lastDrawTime=time;
    ctx.clearRect(0,0,W,H);
    if(!scene){raf=requestAnimationFrame(draw);return}
    // Render static layer once (canes, branches, leaves, clusters, tendrils)
    if(staticDirty)renderStaticLayer();
    // Stamp the static layer
    if(staticCvs)ctx.drawImage(staticCvs,0,0,W,H);
    const t=time*0.001;
    // Only animate: string lights (glow pulse + mouse interaction) and particles
    scene.stringLights.forEach(l=>{
      const pulse=0.3+0.7*((Math.sin(t*l.speed+l.phase)+1)/2);
      const bri=l.brightness*pulse;
      let boost=0;
      if(mouseX>=0){
        const d=dist(l.x,l.y,mouseX,mouseY);
        if(d<150) boost=0.5*(1-d/150);
      }
      const tb=Math.min(bri+boost,1);
      if(tb<0.02)return; // skip nearly-invisible lights
      // Single simplified glow (1 gradient instead of 2)
      const g1=ctx.createRadialGradient(l.x,l.y,0,l.x,l.y,l.glowR*tb);
      g1.addColorStop(0,`rgba(${l.col.r},${l.col.g},${l.col.b},${tb*0.55})`);
      g1.addColorStop(0.4,`rgba(${l.col.r},${l.col.g},${l.col.b},${tb*0.15})`);
      g1.addColorStop(1,"rgba(0,0,0,0)");
      ctx.fillStyle=g1;
      ctx.beginPath();ctx.arc(l.x,l.y,l.glowR*tb,0,TAU);ctx.fill();
      // Bulb — solid fill instead of gradient
      const bulbR=l.baseR*(0.8+tb*0.4);
      ctx.fillStyle=`rgba(${l.col.r},${l.col.g},${l.col.b},${tb*0.9})`;
      ctx.beginPath();ctx.arc(l.x,l.y,bulbR,0,TAU);ctx.fill();
      // Tiny bright center
      ctx.fillStyle=`rgba(255,255,255,${tb*0.7})`;
      ctx.beginPath();ctx.arc(l.x,l.y,bulbR*0.4,0,TAU);ctx.fill();
    });
    if(!reducedMotion){
      scene.particles.forEach(p=>{
        p.x+=p.vx;p.y+=p.vy;p.life+=0.002;
        if(p.y<-10||p.life>1){p.x=(p.phase/TAU)*W;p.y=H+10;p.life=0}
        const alpha=Math.sin(p.life*PI)*0.5*(0.5+0.5*Math.sin(t*p.speed+p.phase));
        if(alpha>0.01){
          // Simple circle instead of radial gradient
          ctx.globalAlpha=alpha;
          ctx.fillStyle=`rgb(${p.col.r},${p.col.g},${p.col.b})`;
          ctx.beginPath();ctx.arc(p.x,p.y,p.r*2,0,TAU);ctx.fill();
          ctx.globalAlpha=1;
        }
      });
    }
    raf=requestAnimationFrame(draw);
  }
  // ======================= SETUP (generalized initialization) =======================
  // Nothing above this line was changed; below: sizing, start / pause, events.
  let dprNow=0,onScreen=true,held=false,alive=true,resizeTimer=null,ro=null,io=null;
  // Battery: `idle` = the lights have settled into the still picture (see the header comment).
  const RUN_FOR=25000,WAKE_FOR=20000;
  const LOW_POWER=(function(){try{return window.matchMedia("(pointer: coarse)").matches||(navigator.hardwareConcurrency||8)<=4}catch(e){return false}})();
  let idle=LOW_POWER,idleTimer=null;
  cvs=canvasEl;
  ctx=cvs.getContext("2d");
  function running(){return alive&&!held&&!idle&&!reducedMotion&&onScreen&&tabVisible&&W>0&&H>0}
  // Start or stop the draw loop to match the current state.
  function sync(){
    if(running()){if(!raf){lastDrawTime=0;raf=requestAnimationFrame(draw);armIdle(RUN_FOR)}}
    else if(raf){cancelAnimationFrame(raf);raf=null}
  }
  function armIdle(ms){clearTimeout(idleTimer);idleTimer=setTimeout(function(){idle=true;sync();paintStill()},ms)}
  // A pointer moving over the hero (or a touch on it) wakes the lights for a while.
  function wake(){if(!alive||reducedMotion)return;if(idle){idle=false;sync()}if(raf)armIdle(WAKE_FOR)}
  // A still picture: the static layer plus one pass of the original draw() for the lights.
  function paintStill(){
    if(raf||!scene||!W||!H)return;
    renderStaticLayer();
    ctx.clearRect(0,0,W,H);
    if(staticCvs)ctx.drawImage(staticCvs,0,0,W,H);
    if(!tabVisible)return;
    lastDrawTime=-1e9;
    draw(performance.now());
    if(raf){cancelAnimationFrame(raf);raf=null} // draw() queues its next frame — not wanted here
  }
  function resize(force){
    if(!alive||!ctx)return;
    const w=hero.offsetWidth,h=hero.offsetHeight;
    if(!w||!h)return; // hidden (display:none, print) — the next resize tries again
    const dpr=Math.min(window.devicePixelRatio||1,2);
    if(force!==true&&scene&&w===W&&h===H&&dpr===dprNow)return; // same size: keep the picture
    const replant=force===true||!scene||w!==W||Math.abs(h-H)>64;
    W=w;H=h;dprNow=dpr;
    cvs.width=Math.round(W*dpr);cvs.height=Math.round(H*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    if(replant)buildScene();else staticDirty=true;
    if(raf)return; // the running loop repaints on its next frame
    paintStill();
    sync();
  }
  function onResize(){clearTimeout(resizeTimer);resizeTimer=setTimeout(resize,150)}
  function onMove(e){
    wake();
    const rect=hero.getBoundingClientRect();
    const pt=e.touches?e.touches[0]:e;
    if(!pt)return;
    mouseX=pt.clientX-rect.left;mouseY=pt.clientY-rect.top;
  }
  function onLeave(){mouseX=-1;mouseY=-1}
  function onVisChange(){tabVisible=!document.hidden;sync()}
  hero.addEventListener("mousemove",onMove);
  hero.addEventListener("touchmove",onMove,{passive:true});
  hero.addEventListener("touchstart",wake,{passive:true});
  hero.addEventListener("mouseleave",onLeave);
  document.addEventListener("visibilitychange",onVisChange);
  window.addEventListener("resize",onResize);
  if("ResizeObserver" in window){ro=new ResizeObserver(onResize);ro.observe(hero)}
  if("IntersectionObserver" in window){
    io=new IntersectionObserver(function(entries){onScreen=entries[entries.length-1].isIntersecting;sync()},{rootMargin:"80px 0px"});
    io.observe(hero);
  }
  tabVisible=!document.hidden;
  reducedMotion=stillArt(); // SETUP: the site's own "Reduce motion" choice counts too
  resize(true);
  sync();
  return {
    hero:hero,
    refresh:function(){resize(true)},
    pause:function(){held=true;sync()},
    resume:function(){held=false;resize();sync()},
    setReducedMotion:function(m){reducedMotion=!!m;sync();if(reducedMotion)paintStill()},
    destroy:function(){
      alive=false;sync();clearTimeout(resizeTimer);clearTimeout(idleTimer);hero.removeEventListener("touchstart",wake);
      if(ro)ro.disconnect();
      if(io)io.disconnect();
      hero.removeEventListener("mousemove",onMove);hero.removeEventListener("touchmove",onMove);hero.removeEventListener("mouseleave",onLeave);
      document.removeEventListener("visibilitychange",onVisChange);window.removeEventListener("resize",onResize);
      mouseX=-1;mouseY=-1;
    },
  };
  }

  // ---------- find the heroes and give each one its own art ----------
  const HERO_SEL="[data-gv-hero], .page-hero, #homeHero";
  const arts=[];
  const seen=new WeakSet();
  function findCanvas(hero){
    for(let i=0;i<hero.children.length;i++){
      const c=hero.children[i];
      if(c.tagName==="CANVAS"&&(c.classList.contains("gv-hero-canvas")||c.id==="grapevineCanvas"))return c;
    }
    return null;
  }
  function init(root){
    const scope=root&&root.querySelectorAll?root:document;
    const list=[];
    if(scope.matches&&scope.matches(HERO_SEL))list.push(scope);
    scope.querySelectorAll(HERO_SEL).forEach(function(h){list.push(h)});
    list.forEach(function(hero){
      if(seen.has(hero))return;
      seen.add(hero);
      let cvs=findCanvas(hero);
      if(!cvs){
        cvs=document.createElement("canvas");
        cvs.className="gv-hero-canvas";
        cvs.setAttribute("aria-hidden","true");
        hero.insertBefore(cvs,hero.firstChild);
      }
      if(!cvs.getContext||!cvs.getContext("2d"))return;
      try{arts.push(createGrapevineArt(hero,cvs))}
      catch(e){if(window.console)console.warn("[hero art]",e)}
    });
  }
  function onMotionPref(){arts.forEach(function(a){a.setReducedMotion(stillArt())})}
  if(RM.addEventListener)RM.addEventListener("change",onMotionPref);
  else if(RM.addListener)RM.addListener(onMotionPref);
  window.addEventListener("gvlv:prefs",onMotionPref); // the panel's Motion choice (app.js GV.prefs)
  // data-saver / data-motion changed by anything (the panel, going offline, the connection): follow at once
  if(window.MutationObserver)new MutationObserver(onMotionPref).observe(document.documentElement,{attributes:true,attributeFilter:["data-saver","data-motion"]});

  window.GVHeroArt={
    init:init,
    arts:arts,
    pause:function(){arts.forEach(function(a){a.pause()})},
    resume:function(){arts.forEach(function(a){a.resume()})},
  };
  window.GV_CANVAS={start:function(){init(document);window.GVHeroArt.resume()},stop:function(){window.GVHeroArt.pause()}};
  function boot(){init(document)}
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot);else boot();
})();
