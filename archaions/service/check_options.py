import asyncio,json,sys
from pathlib import Path
from playwright.async_api import async_playwright
from run_options import KITS,DNA_MODS,RNA_MODS,BARCODE_KITS
root=Path(__file__).parent
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
  page=await browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  async def assets(route):
   name=route.request.url.split('/basecalling/')[1].split('?')[0] or 'index.html'
   await route.fulfill(path=str(root/'web'/name))
  async def api(route):
   await route.fulfill(json={'worker_online':True,'max_file_bytes':2147483648,'chunk_bytes':8388608,'gpus':[{'id':'B300','available':True,'name':'B300','models':['hac','sup']}],'kits':KITS,'dna_modifications':DNA_MODS,'rna_modifications':RNA_MODS,'barcode_kits':sorted(BARCODE_KITS)} if route.request.url.endswith('/health') else [])
  await page.route('**/basecalling/**',assets);await page.route('**/basecalling-public-api/**',api)
  await page.goto('https://archaions.com/basecalling/',wait_until='networkidle')
  assert await page.locator('#kit option').count()==len(KITS)
  await page.locator('[data-mod-base="A"]').select_option('6mA')
  await page.locator('[data-mod-base="C"]').select_option('4mC_5mC')
  assert (await page.evaluate('runOptions()'))['modifications']==['6mA','4mC_5mC']
  await page.locator('#kit').select_option('SQK-RNA004')
  assert (await page.evaluate('runOptions()'))['modifications']==[]
  assert await page.locator('#demultiplex').is_disabled()
  await page.locator('#model').select_option('sup')
  assert await page.locator('[data-mod-base="G"]').count()==1
  await page.locator('#kit').select_option('SQK-NBD114-96');await page.locator('#demultiplex').check();await page.locator('#barcodeBothEnds').check()
  await page.locator('#kit').select_option('SQK-RBK114-96')
  assert not (await page.evaluate('runOptions()'))['barcode_both_ends']
  await page.locator('#files').set_input_files({'name':'test.pod5','mimeType':'application/octet-stream','buffer':b'abc'})
  assert await page.locator('#uploadButton').is_enabled()
  await page.evaluate('state.busy=true;controls()')
  assert await page.locator('#kit').is_disabled() and await page.locator('#qc').is_disabled()
  await page.evaluate('state.busy=false;controls()')
  for width in [1440,390,320]:
   await page.set_viewport_size({'width':width,'height':950})
   assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
   await page.screenshot(path=str(root/f'options-{width}.png'),full_page=True)
  assert not errors,errors
  print('PASS: kit/mode modifications, demultiplexing, immutable controls, mobile layout, JavaScript errors')
  await browser.close()
asyncio.run(main())
