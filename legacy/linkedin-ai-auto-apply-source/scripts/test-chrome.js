const path = require('path');
const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());

const ROOT = path.resolve(__dirname, '..');
const CHROME_EXECUTABLE_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CHROME_USER_DATA_DIR = path.join(ROOT, 'chrome-profile-linkedin');
const CHROME_PROFILE = 'Default';

async function main() {
    console.log('Launching browser with ROOT:', ROOT);
    const browser = await puppeteer.launch({
        headless: false,
        executablePath: CHROME_EXECUTABLE_PATH,
        args: [
            `--user-data-dir=${CHROME_USER_DATA_DIR}`,
            `--profile-directory=${CHROME_PROFILE}`,
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--window-size=1365,900',
            `--disable-extensions-except=${ROOT}`,
            `--load-extension=${ROOT}`
        ],
        ignoreDefaultArgs: ['--disable-extensions'],
        defaultViewport: null
    });

    console.log('Browser launched. Targets:');
    const targets = browser.targets();
    for (const target of targets) {
        console.log(`- Type: ${target.type()}, URL: ${target.url()}`);
    }

    const [page] = await browser.pages();
    await page.goto('chrome://extensions/');
    await new Promise(r => setTimeout(r, 3000));
    const extNames = await page.evaluate(() => {
        try {
            const manager = document.querySelector('extensions-manager');
            const items = manager.shadowRoot.querySelectorAll('extensions-item');
            return Array.from(items).map(item => {
                const name = item.shadowRoot.querySelector('#name').textContent.trim();
                const active = item.shadowRoot.querySelector('#enableToggle').checked;
                const id = item.id;
                return { name, active, id };
            });
        } catch (e) {
            return { error: e.message, html: document.body.innerText };
        }
    });
    console.log('Extensions listed on chrome://extensions/:', JSON.stringify(extNames, null, 2));

    // Keep it open for 5 seconds to inspect
    await new Promise(r => setTimeout(r, 2000));
    await browser.close();
}

main().catch(console.error);
