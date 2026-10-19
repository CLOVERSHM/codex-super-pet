const path = require("path");
const fs = require("fs");
const sharp = require("sharp");
const root = path.resolve(__dirname, "..");
const size = 128;
const pixels = Buffer.alloc(size * size * 4);
for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
  const k = (y * size + x) * 4;
  const background = [23, 109, 80, 255];
  const row = Math.floor((y - 26) / 26);
  const glyph = row >= 0 && row < 3 && y % 26 >= 0 &&
    ((x >= 26 && x < 40 && y >= 26 + row * 26 && y < 40 + row * 26) ||
    (x >= 52 && x < 102 - row * 12 && y >= 29 + row * 26 && y < 37 + row * 26));
  const color = glyph ? (row === 1 ? [238, 198, 96, 255] : [240, 251, 244, 255]) : background;
  pixels.set(color, k);
}
(async () => {
  const output = await sharp(pixels, {raw: {width:size, height:size, channels:4}}).png().toBuffer();
  for (const relative of ["web/icon.png", "plugins/delivery-console/assets/icon.png"]) {
    const file = path.join(root, relative);
    fs.mkdirSync(path.dirname(file), {recursive:true});
    fs.writeFileSync(file, output);
  }
})();
