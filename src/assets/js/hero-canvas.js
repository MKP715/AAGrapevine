/* Grapevine hero art — animated vineyard with string lights.
   Original artwork code by the NETA 65 GV/LV committee web servant (carried over
   unchanged from the previous site). Draws into <canvas id="grapevineCanvas">
   inside the element with id="homeHero". Auto-starts; honors reduced motion. */
const GV_CANVAS = (function(){
  let cvs,ctx,W,H,raf=null,scene=null,mouseX=-1,mouseY=-1;
  // Offscreen canvas for static elements (canes, branches, leaves, clusters, tendrils)
  let staticCvs=null,staticCtx=null,staticDirty=true;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
  function resize(){
    const hero=document.getElementById("homeHero");
    if(!hero||!cvs)return;
    const dpr=Math.min(window.devicePixelRatio||1,2);
    W=hero.offsetWidth;H=hero.offsetHeight;
    cvs.width=W*dpr;cvs.height=H*dpr;
    cvs.style.width=W+"px";cvs.style.height=H+"px";
    ctx.setTransform(dpr,0,0,dpr,0,0);
    buildScene();
    // Reduced motion: no draw loop runs, so paint the still picture for the new size here.
    if(reducedMotion){renderStaticLayer();ctx.drawImage(staticCvs,0,0,W,H)}
  }
  function onMove(e){
    const hero=document.getElementById("homeHero");
    if(!hero)return;
    const rect=hero.getBoundingClientRect();
    const cx=e.touches?e.touches[0].clientX:e.clientX;
    const cy=e.touches?e.touches[0].clientY:e.clientY;
    mouseX=cx-rect.left;mouseY=cy-rect.top;
  }
  function onLeave(){mouseX=-1;mouseY=-1}
  let _resizeTimer=null;
  function onResize(){clearTimeout(_resizeTimer);_resizeTimer=setTimeout(resize,200)}
  function onVisChange(){tabVisible=!document.hidden}
  return {
    start:function(){
      cvs=document.getElementById("grapevineCanvas");
      if(!cvs)return;
      if(raf){cancelAnimationFrame(raf);raf=null}
      ctx=cvs.getContext("2d");
      if(!ctx)return;
      // Follow the hero's size in both modes (rotation, window resize, the synthetic
      // "resize" home.js sends once the web fonts have settled the hero's height).
      window.addEventListener("resize",onResize);
      resize(); // with reduced motion this also paints the still picture
      if(reducedMotion)return;
      lastDrawTime=0;
      raf=requestAnimationFrame(draw);
      const hero=document.getElementById("homeHero");
      if(hero){hero.addEventListener("mousemove",onMove);hero.addEventListener("touchmove",onMove,{passive:true});hero.addEventListener("mouseleave",onLeave)}
      document.addEventListener("visibilitychange",onVisChange);
    },
    stop:function(){
      if(raf){cancelAnimationFrame(raf);raf=null}
      const hero=document.getElementById("homeHero");
      if(hero){hero.removeEventListener("mousemove",onMove);hero.removeEventListener("touchmove",onMove);hero.removeEventListener("mouseleave",onLeave)}
      window.removeEventListener("resize",onResize);
      document.removeEventListener("visibilitychange",onVisChange);
      mouseX=-1;mouseY=-1;
    },
  };
})();
window.GV_CANVAS = GV_CANVAS;
(function () {
  function boot() { if (document.getElementById("grapevineCanvas")) GV_CANVAS.start(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
