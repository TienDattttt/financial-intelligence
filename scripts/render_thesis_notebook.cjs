/** Render computed Plotly figures and tables using the existing local browser.
 * Verified Sources: installed frontend/node_modules/@playwright/test API;
 * installed Plotly.js runtime supplied by Python plotly.offline.get_plotlyjs.
 */
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(path.resolve(__dirname, '../frontend/node_modules/@playwright/test'));

const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function main() {
  const out = path.resolve(process.argv[2]);
  const outputsRoot = path.resolve(__dirname, '../notebooks/outputs');
  if (!out.startsWith(outputsRoot + path.sep)) throw new Error('Unexpected artifact destination');
  const notebookName = process.argv[3] || 'finintel_3_4_tien_xu_ly_du_lieu.ipynb';
  const reportName = process.argv[4] || 'bao_cao_3_4.html';
  const reportTitle = process.argv[5] || '3.4. Tiền xử lý và đánh giá chất lượng dữ liệu FinIntel';
  const entries = JSON.parse(fs.readFileSync(path.join(out, 'render_entries.json'), 'utf8'));
  const notebook = JSON.parse(fs.readFileSync(path.resolve(out, '..', '..', notebookName), 'utf8'));
  const asOf = notebook.metadata.finintel.as_of.split('-').reverse().join('/');
  const sourceNote = `Nguồn: bản sao thực nghiệm FinIntel, mốc thông tin ${asOf}.`;
  const browser = await chromium.launch({channel:'msedge',headless:true});
  const page = await browser.newPage({viewport:{width:1500,height:1000},deviceScaleFactor:1.5});
  const report = [];
  try {
    await page.setContent('<html><meta charset="utf-8"><body style="margin:0;background:white"><div id="figure"></div></body></html>');
    await page.addScriptTag({path:path.join(out,'plotly.min.js')});
    for (const entry of entries) {
      const stem = String(entry.number).padStart(2,'0');
      const dest = path.join(out,`${stem}.png`);
      if (entry.kind === 'table') {
        await page.evaluate(html => {
          document.body.innerHTML = '<main id="table-container" style="width:1380px;margin:0">'+html+'</main>';
        }, entry.html);
        await page.evaluate(() => document.fonts.ready);
        await page.locator('#table-container').screenshot({path:dest});
      } else {
        const png = await page.evaluate(async figure => {
          document.body.innerHTML = '<div id="figure"></div>';
          await Plotly.newPlot('figure',figure.data,figure.layout,{staticPlot:true,responsive:false});
          await document.fonts.ready;
          return await Plotly.toImage('figure',{format:'png',width:1200,height:figure.layout.height || 620,scale:2});
        },entry.figure);
        await page.evaluate(({png,caption,sourceNote}) => {
          const main=document.createElement('main');
          main.id='chart-container';
          main.style.cssText='width:1260px;padding:16px 30px 22px;box-sizing:border-box;font-family:Arial,sans-serif;color:#172b3a;background:white';
          const image=document.createElement('img');image.src=png;image.style.cssText='display:block;width:1200px;height:auto';
          const note=document.createElement('p');note.textContent=caption;note.style.cssText='font-size:18px;line-height:1.5;margin:0 15px 12px';
          const source=document.createElement('p');source.textContent=sourceNote;source.style.cssText='font-size:13px;color:#586a7c;margin:0 15px';
          main.append(image,note,source);document.body.replaceChildren(main);
        },{png,caption:entry.caption,sourceNote});
        await page.locator('#chart-container img').evaluate(image=>image.decode());
        await page.locator('#chart-container').screenshot({path:dest});
      }
      report.push(`<section><h2>${escape(entry.title)}</h2><img src="${stem}.png" alt="${escape(entry.title)}"><p>${escape(entry.caption)}</p></section>`);
      console.log(`Rendered ${stem}: ${entry.title}`);
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(out, reportName), `<!doctype html><html lang="vi"><meta charset="utf-8"><title>${escape(reportTitle)}</title><style>body{font-family:Arial,sans-serif;background:#edf1f5;color:#172b3a;margin:0}header,section{max-width:1400px;background:white;margin:24px auto;padding:28px;box-sizing:border-box}h1{font-size:30px}h2{font-size:24px}p{line-height:1.6;font-size:18px}img{display:block;max-width:100%;height:auto}section{break-inside:avoid}@media print{body{background:white}header,section{margin:0;padding:12px;box-shadow:none}}</style><header><h1>${escape(reportTitle)}</h1><p>Kết quả tính trực tiếp trên bản sao dữ liệu thực nghiệm. Ảnh có thể chèn trực tiếp vào Word; mở từng ảnh để xem ở kích thước gốc.</p></header>${report.join('\n')}</html>`, 'utf8');
}
main().catch(error => {console.error(error);process.exitCode=1;});
