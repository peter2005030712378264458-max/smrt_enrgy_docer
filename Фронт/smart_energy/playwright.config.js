import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir:'./tests/ux', use:{baseURL:'http://127.0.0.1:5179',trace:'retain-on-failure'},
  webServer:{ command:'npm run dev -- --host 127.0.0.1 --port 5179 --strictPort',url:'http://127.0.0.1:5179',reuseExistingServer:false },
  projects:[{name:'desktop',use:{browserName:'chromium',viewport:{width:1440,height:1000}}},
            {name:'mobile',use:{browserName:'chromium',viewport:{width:390,height:844}}}]
})
