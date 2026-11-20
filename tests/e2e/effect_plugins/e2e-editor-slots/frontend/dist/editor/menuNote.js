export default {
	version: 1,
	available: (ctx) => (ctx.hasSource ? true : 'Open a picture first'),
	run: () => {
		window.__e2eMenuNote = (window.__e2eMenuNote ?? 0) + 1;
	}
};
