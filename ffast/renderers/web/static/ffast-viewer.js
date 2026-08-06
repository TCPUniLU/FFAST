/**
 * FFAST Web Renderer — entry point.
 *
 * Connects to ffast-server over WebSocket, performs the HELLO handshake,
 * receives Scene Snapshots and Patches, and renders a molecular scene using
 * Three.js InstancedMesh (atoms), LineSegments (bonds), and ArrowHelper (forces).
 *
 * Split into native ES modules (ADR 0045 decision #2): msgpack.js (codec),
 * connection.js (FFastConnection), renderer.js (MoleculeRenderer), app.js
 * (FFastApp). This file is just the bootstrap.
 *
 * A popped-out 3D view is `?mode=loupe-live`, handled by FFastApp itself: it
 * opens its own controlling connection (ADR 0044 Phase 4). The socket-less
 * BroadcastChannel satellite that preceded it is gone (ADR 0051).
 */

import { FFastApp } from './app.js';

const app = new FFastApp();

// Expose the live app for debugging and the Playwright runtime tests (which
// compute an atom's screen position via app.renderer to drive a real pick).
window.ffastApp = app;
