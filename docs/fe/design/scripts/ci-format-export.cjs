// Temporary one-run diagnostic: emit formatted source from CI so it can be committed without changing product code.
const fs = require('node:fs');

const files = [
  'src/catalog.ts',
  'src/catalog.test.ts',
  'src/ScreenPreview.tsx',
  'src/ScreenPreview.test.tsx',
  'src/screens/PolicyScreens.tsx',
];
for (const path of files) {
  const source = fs.readFileSync(path, 'utf8');
  for (let offset = 0; offset < source.length; offset += 3000) {
    const chunk = source.slice(offset, offset + 3000);
    console.log('PRETTY_SYNC|' + path + '|' + offset + '|' + JSON.stringify(chunk));
  }
}
