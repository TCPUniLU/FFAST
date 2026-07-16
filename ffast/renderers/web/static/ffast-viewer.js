/**
 * FFAST Web Renderer — entry point.
 *
 * Connects to ffast-server over WebSocket, performs the HELLO handshake,
 * receives Scene Snapshots and Patches, and renders a molecular scene using
 * Three.js InstancedMesh (atoms), LineSegments (bonds), and ArrowHelper (forces).
 *
 * Split into native ES modules (ADR 0045 decision #2): msgpack.js (codec),
 * connection.js (FFastConnection), renderer.js (MoleculeRenderer), app.js
 * (FFastApp), satellite.js (LoupeSatelliteApp). This file is just the
 * bootstrap — pick the main app, or a popped-out satellite Loupe when opened
 * with ?mode=loupe.
 */

import { FFastApp } from './app.js';
import { LoupeSatelliteApp } from './satellite.js';

const _params = new URLSearchParams(window.location.search);
const app = (_params.get('mode') === 'loupe' && typeof BroadcastChannel !== 'undefined')
  ? new LoupeSatelliteApp(_params.get('ch') || 'ffast-loupe')
  : new FFastApp();
