// Side-effect-only: importing this module registers every built-in generation
// message handler on generationMessageRegistry. Import once (from
// stores/generation.ts) before any WS message is dispatched.
import './status';
import './workbenchUpdate';
import './galleryUpdate';
import './pipeArtifact';
import './timerUpdate';
import './complete';
import './error';
import './queueUpdate';
