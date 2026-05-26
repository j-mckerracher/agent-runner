/*
 * Animated background canvas.
 *
 * This module is isolated from the rest of the GUI so rendering concerns do not
 * obscure application logic. It depends only on Three.js from the import map in
 * index.html and cleans up listeners on pagehide.
 */

import * as THREE from "three";

(function initBackground() {
    const canvas = document.getElementById("bg-canvas");
    if (!canvas) return;

    let renderer,
        scene,
        camera,
        animId = 0,
        frameCount = 0;
    let icoMesh, innerIco, particles, linesMesh;
    let particlePositions;
    const velocities = [];
    const nodeCount = 140;
    const maxLines = 350;

    let mouseX = 0,
        mouseY = 0,
        camTargetX = 0,
        camTargetY = 0;

    const w0 = window.innerWidth,
        h0 = window.innerHeight;
    renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: true,
    });
    renderer.setSize(w0, h0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(55, w0 / h0, 0.1, 600);
    camera.position.set(0, 0, 130);

    // Outer wireframe icosahedron — gold
    {
        const g = new THREE.IcosahedronGeometry(52, 1);
        const wf = new THREE.WireframeGeometry(g);
        const mat = new THREE.LineBasicMaterial({
            color: 0xe8a020,
            transparent: true,
            opacity: 0.4,
            blending: THREE.AdditiveBlending,
        });
        icoMesh = new THREE.LineSegments(wf, mat);
        scene.add(icoMesh);
        g.dispose();
    }

    // Inner wireframe icosahedron — blue
    {
        const g = new THREE.IcosahedronGeometry(28, 1);
        const wf = new THREE.WireframeGeometry(g);
        const mat = new THREE.LineBasicMaterial({
            color: 0x4a8ef7,
            transparent: true,
            opacity: 0.32,
            blending: THREE.AdditiveBlending,
        });
        innerIco = new THREE.LineSegments(wf, mat);
        scene.add(innerIco);
        g.dispose();
    }

    // Particles
    particlePositions = new Float32Array(nodeCount * 3);
    for (let i = 0; i < nodeCount; i++) {
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = 38 + Math.random() * 55;
        particlePositions[i * 3] =
            r * Math.sin(phi) * Math.cos(theta);
        particlePositions[i * 3 + 1] =
            r * Math.sin(phi) * Math.sin(theta);
        particlePositions[i * 3 + 2] = r * Math.cos(phi);
        velocities.push({
            x: (Math.random() - 0.5) * 0.04,
            y: (Math.random() - 0.5) * 0.04,
            z: (Math.random() - 0.5) * 0.04,
        });
    }

    // Sprite texture for particles
    const spriteCanvas = document.createElement("canvas");
    spriteCanvas.width = 32;
    spriteCanvas.height = 32;
    const sctx = spriteCanvas.getContext("2d");
    const grad = sctx.createRadialGradient(16, 16, 0, 16, 16, 16);
    grad.addColorStop(0, "rgba(74, 142, 247, 1)");
    grad.addColorStop(0.45, "rgba(74, 142, 247, 0.5)");
    grad.addColorStop(1, "rgba(74, 142, 247, 0)");
    sctx.fillStyle = grad;
    sctx.fillRect(0, 0, 32, 32);
    const spriteTex = new THREE.CanvasTexture(spriteCanvas);

    {
        const geo = new THREE.BufferGeometry();
        geo.setAttribute(
            "position",
            new THREE.BufferAttribute(particlePositions, 3),
        );
        const mat = new THREE.PointsMaterial({
            size: 3.0,
            map: spriteTex,
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
        });
        particles = new THREE.Points(geo, mat);
        scene.add(particles);
    }

    // Connection lines
    {
        const geo = new THREE.BufferGeometry();
        const pos = new Float32Array(maxLines * 6);
        geo.setAttribute(
            "position",
            new THREE.BufferAttribute(pos, 3),
        );
        geo.setDrawRange(0, 0);
        const mat = new THREE.LineBasicMaterial({
            color: 0x4a8ef7,
            transparent: true,
            opacity: 0.35,
            blending: THREE.AdditiveBlending,
        });
        linesMesh = new THREE.LineSegments(geo, mat);
        scene.add(linesMesh);
    }

    function updateParticles() {
        const bounds = 92;
        const pos = particlePositions;
        for (let i = 0; i < nodeCount; i++) {
            const ix = i * 3,
                iy = ix + 1,
                iz = ix + 2;
            pos[ix] += velocities[i].x;
            pos[iy] += velocities[i].y;
            pos[iz] += velocities[i].z;
            const rSq =
                pos[ix] * pos[ix] +
                pos[iy] * pos[iy] +
                pos[iz] * pos[iz];
            if (rSq > bounds * bounds) {
                const r = Math.sqrt(rSq);
                const nx = pos[ix] / r,
                    ny = pos[iy] / r,
                    nz = pos[iz] / r;
                const dot =
                    velocities[i].x * nx +
                    velocities[i].y * ny +
                    velocities[i].z * nz;
                if (dot > 0) {
                    velocities[i].x -= 2 * dot * nx;
                    velocities[i].y -= 2 * dot * ny;
                    velocities[i].z -= 2 * dot * nz;
                }
            }
        }
        particles.geometry.attributes.position.needsUpdate = true;
    }

    function updateLines() {
        const threshold = 24;
        const linePos =
            linesMesh.geometry.attributes.position.array;
        const pPos = particlePositions;
        let lineIdx = 0;
        for (let i = 0; i < nodeCount && lineIdx < maxLines; i++) {
            for (
                let j = i + 1;
                j < nodeCount && lineIdx < maxLines;
                j++
            ) {
                const dx = pPos[i * 3] - pPos[j * 3];
                const dy = pPos[i * 3 + 1] - pPos[j * 3 + 1];
                const dz = pPos[i * 3 + 2] - pPos[j * 3 + 2];
                if (
                    dx * dx + dy * dy + dz * dz <
                    threshold * threshold
                ) {
                    linePos[lineIdx * 6] = pPos[i * 3];
                    linePos[lineIdx * 6 + 1] = pPos[i * 3 + 1];
                    linePos[lineIdx * 6 + 2] = pPos[i * 3 + 2];
                    linePos[lineIdx * 6 + 3] = pPos[j * 3];
                    linePos[lineIdx * 6 + 4] = pPos[j * 3 + 1];
                    linePos[lineIdx * 6 + 5] = pPos[j * 3 + 2];
                    lineIdx++;
                }
            }
        }
        linesMesh.geometry.setDrawRange(0, lineIdx * 2);
        linesMesh.geometry.attributes.position.needsUpdate = true;
    }

    function animate() {
        animId = requestAnimationFrame(animate);
        frameCount++;

        icoMesh.rotation.x += 0.0018;
        icoMesh.rotation.y += 0.0025;
        innerIco.rotation.x -= 0.003;
        innerIco.rotation.y += 0.004;
        innerIco.rotation.z += 0.001;

        updateParticles();
        if (frameCount % 4 === 0) updateLines();

        camTargetX += (mouseX * 18 - camTargetX) * 0.04;
        camTargetY += (-mouseY * 10 - camTargetY) * 0.04;
        camera.position.x = camTargetX;
        camera.position.y = camTargetY;
        camera.lookAt(0, 0, 0);

        renderer.render(scene, camera);
    }

    function onMouseMove(e) {
        mouseX = (e.clientX / window.innerWidth - 0.5) * 2;
        mouseY = (e.clientY / window.innerHeight - 0.5) * 2;
    }
    function onResize() {
        const w = window.innerWidth,
            h = window.innerHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("resize", onResize);
    window.addEventListener("pagehide", () => {
        cancelAnimationFrame(animId);
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("resize", onResize);
        renderer && renderer.dispose();
    });

    animate();
})();
