/* Небольшая WebGL-сцена без движка. Запускается только на широком экране. */
'use strict';
window.startByteScene = function startByteScene() {
  const canvas = document.getElementById('hero-canvas');
  const host = document.getElementById('hero-art');
  if (!canvas || !host) return;
  const gl = canvas.getContext('webgl', { alpha: true, antialias: false, powerPreference: 'low-power', preserveDrawingBuffer: true });
  if (!gl) return;
  const vertex = 'attribute vec2 position; void main(){gl_Position=vec4(position,0.,1.);}';
  const fragment = `
    precision highp float;
    uniform vec2 resolution;
    uniform vec2 pointer;
    uniform float time;
    uniform float scroll;
    mat2 rotate(float a){float s=sin(a),c=cos(a);return mat2(c,-s,s,c);}
    vec3 local(vec3 p){
      p.xz=rotate(-.35+pointer.x*.6+time*.13+scroll*1.5)*p.xz;
      p.yz=rotate(.80+pointer.y*.38+sin(time*.21)*.10-scroll*.5)*p.yz;
      p.xy=rotate(-.66+sin(time*.12)*.06)*p.xy;
      return p;
    }
    float surface(vec3 pos){
      vec3 p=local(pos);
      float a=atan(p.z,p.x);
      float r=1.02+.055*cos(a*3.);
      vec2 q=vec2(length(p.xz)-r,p.y-.09*sin(a*3.));
      q=rotate(a*1.5+time*.07)*q;
      vec2 d=abs(q)-vec2(.095,.21);
      return length(max(d,0.))+min(max(d.x,d.y),0.)-.16;
    }
    vec3 normal(vec3 p){
      vec2 e=vec2(.002,0.);
      return normalize(vec3(surface(p+e.xyy)-surface(p-e.xyy),surface(p+e.yxy)-surface(p-e.yxy),surface(p+e.yyx)-surface(p-e.yyx)));
    }
    vec3 environment(vec3 r){
      vec3 color=vec3(.025,.028,.055);
      color+=vec3(.34,.25,.95)*pow(max(dot(r,normalize(vec3(-.8,.3,1.))),0.),5.)*2.4;
      color+=vec3(.18,.75,1.)*pow(max(dot(r,normalize(vec3(.7,.8,.4))),0.),12.)*2.8;
      color+=vec3(.84,.91,1.)*pow(max(dot(r,normalize(vec3(-.6,1.,.1))),0.),40.)*3.3;
      color+=vec3(.45,.25,1.)*pow(max(dot(r,normalize(vec3(.2,-.8,.5))),0.),18.)*1.7;
      float stripe=pow(max(0.,1.-abs(dot(r,normalize(vec3(.3,.7,.5)))-.22)*12.),5.);
      color+=vec3(.54,.7,1.)*stripe*.65;
      return color;
    }
    void main(){
      vec2 uv=(gl_FragCoord.xy-.5*resolution)/resolution.y;
      vec3 ro=vec3(0.,0.,4.8),rd=normalize(vec3(uv*3.5,-4.2));
      float depth=0.,hit=0.;
      for(int i=0;i<72;i++){
        vec3 p=ro+rd*depth;
        float distance=surface(p);
        if(distance<.0018){hit=1.;break;}
        depth+=distance*.78;
        if(depth>8.)break;
      }
      if(hit<.5){gl_FragColor=vec4(0.);return;}
      vec3 p=ro+rd*depth,n=normal(p),v=-rd;
      vec3 reflected=reflect(rd,n);
      float fresnel=pow(1.-max(dot(n,v),0.),3.);
      vec3 lp=local(p);
      float variation=.5+.5*sin(atan(lp.z,lp.x)*2.+lp.y*3.);
      vec3 base=mix(vec3(.24,.17,.64),vec3(.15,.42,.69),variation);
      vec3 light=normalize(vec3(-2.,3.,4.));
      float diffuse=max(dot(n,light),0.);
      float spec=pow(max(dot(reflect(-light,n),v),0.),65.);
      vec3 color=base*(.18+diffuse*.24)+environment(reflected)*(.6+fresnel*.65)+vec3(.76,.85,1.)*spec*1.8;
      color+=vec3(.12,.24,.75)*fresnel*.35;
      color=color/(color+vec3(.65));
      color=pow(color,vec3(.85));
      gl_FragColor=vec4(color,1.);
    }`;
  function shader(type, source) {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) return null;
    return s;
  }
  const vs = shader(gl.VERTEX_SHADER, vertex), fs = shader(gl.FRAGMENT_SHADER, fragment);
  if (!vs || !fs) return;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
  gl.useProgram(program);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, 'position');
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const uniforms = Object.fromEntries(['resolution', 'pointer', 'time', 'scroll'].map(k => [k, gl.getUniformLocation(program,k)]));
  let visible = true, paused = false, frame = null, last = 0, elapsed = 0;
  let pointerX = 0, pointerY = 0, currentX = 0, currentY = 0;
  function resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 1.15);
    canvas.width = Math.min(850, Math.round(host.clientWidth * ratio));
    canvas.height = Math.round(host.clientHeight * ratio);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(uniforms.resolution, canvas.width, canvas.height);
  }
  function render(now) {
    frame = null;
    if (!visible || document.hidden || paused) return;
    if (now - last >= 1000/30) {
      elapsed += Math.min(now-last, 60)/1000;
      last = now;
      currentX += (pointerX-currentX)*.075;
      currentY += (pointerY-currentY)*.075;
      gl.uniform2f(uniforms.pointer,currentX,currentY);
      gl.uniform1f(uniforms.time,elapsed);
      gl.uniform1f(uniforms.scroll,window.byteSceneProgress||0);
      gl.drawArrays(gl.TRIANGLES,0,6);
    }
    frame = requestAnimationFrame(render);
  }
  function resume() { if (!frame && visible && !paused && !document.hidden) {last=performance.now();frame=requestAnimationFrame(render);} }
  host.addEventListener('pointermove',e=>{const r=host.getBoundingClientRect();pointerX=(e.clientX-r.left)/r.width-.5;pointerY=(e.clientY-r.top)/r.height-.5;},{passive:true});
  host.addEventListener('pointerleave',()=>{pointerX=0;pointerY=0;});
  const observer = new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)resume();else if(frame){cancelAnimationFrame(frame);frame=null;}},{threshold:.05});
  observer.observe(host);
  document.addEventListener('visibilitychange',resume);
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  const toggle=document.createElement('button');
  toggle.type='button';toggle.className='art-toggle';toggle.textContent='Приостановить 3D';toggle.setAttribute('aria-pressed','false');
  toggle.onclick=()=>{paused=!paused;toggle.textContent=paused?'Продолжить 3D':'Приостановить 3D';toggle.setAttribute('aria-pressed',String(paused));if(paused&&frame){cancelAnimationFrame(frame);frame=null;}else resume();};
  // Кнопка вынесена из декоративной области для клавиатурного доступа.
  host.removeAttribute('aria-hidden');canvas.setAttribute('aria-hidden','true');
  host.append(toggle);
  resize();host.classList.add('webgl-ready');resume();
  return ()=>{if(frame)cancelAnimationFrame(frame);observer.disconnect();resizeObserver.disconnect();gl.deleteBuffer(buffer);gl.deleteProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);canvas.width=0;canvas.height=0;host.classList.remove('webgl-ready');toggle.remove();};
};
