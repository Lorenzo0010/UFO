const esbuild = require('esbuild');
const fs = require('fs');
const path = require('path');

const PROVIDERS_DIR = path.join(__dirname, 'providers');
const SRC_DIR = path.join(__dirname, 'src');

async function build() {
    if (!fs.existsSync(PROVIDERS_DIR)) {
        fs.mkdirSync(PROVIDERS_DIR);
    }

    const providers = ['vidxgo', 'vixcloud', 'guardoserie', 'guardahd', 'animeunity', 'animeworld', 'animesaturn', 'cinemacity', 'altadefinizionestreaming', 'netmirror'];

    for (const provider of providers) {
        const entryPoint = path.join(SRC_DIR, provider, 'index.js');
        const outFile = path.join(PROVIDERS_DIR, `${provider}.js`);

        if (!fs.existsSync(entryPoint)) {
            console.warn(`Skipping ${provider}: index.js not found.`);
            continue;
        }

        console.log(`Building ${provider}...`);

        try {
            await esbuild.build({
                entryPoints: [entryPoint],
                outfile: outFile,
                bundle: true,
                minify: true,
                platform: 'neutral',
                target: ['es2016'],
                format: 'cjs',
                external: ['fs', 'path', 'https', 'http', 'url', 'crypto', 'undici', 'cheerio', 'axios']
            });
            console.log(`✅ Built ${provider}`);
        } catch (e) {
            console.error(`❌ Failed to build ${provider}:`, e.message);
        }
    }
}

build().catch(console.error);
