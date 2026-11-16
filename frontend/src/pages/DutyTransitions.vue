<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const zones = ref([])
const zoneFilter = ref('')
const handoverFilter = ref('')
const err = ref('')

async function load() {
  err.value = ''
  try {
    const params = new URLSearchParams()
    if (zoneFilter.value) params.set('zone_id', zoneFilter.value)
    if (handoverFilter.value) params.set('handover_id', handoverFilter.value)
    const qs = params.toString()
    rows.value = await getJson(`/api/duty-transitions${qs ? `?${qs}` : ''}`)
  } catch (e) {
    err.value = String(e.message || e)
  }
}

onMounted(async () => {
  try {
    zones.value = await getJson('/api/zones')
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>值班切换证明</h1>
    <p class="lead">
      每次完成交班逐区落持久化证明：责任区、旧值班、新值班、切换时刻、交班单 id；
      只追加不改写，与值班权、交班单同库同源，可按责任区或交班单 id 查询。
    </p>
    <div class="panel">
      <div class="row">
        <label>按责任区
          <select v-model="zoneFilter" @change="load">
            <option value="">全部责任区</option>
            <option v-for="z in zones" :key="z.id" :value="z.id">{{ z.name }}</option>
          </select>
        </label>
        <label>按交班单 id
          <input
            v-model="handoverFilter"
            placeholder="如 1"
            style="width:120px"
            @keyup.enter="load"
          />
        </label>
        <button class="btn ghost" @click="load">查询</button>
      </div>
      <p v-if="err" class="msg">{{ err }}</p>
      <table>
        <thead>
          <tr>
            <th>证明</th><th>责任区</th><th>旧值班班次</th><th>新值班班次</th>
            <th>切换时刻</th><th>交班单</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in rows" :key="t.id">
            <td>#{{ t.id }}</td>
            <td>{{ t.zone_name }} <span class="tag">{{ t.zone_code }}</span></td>
            <td>
              <span v-if="t.from_shift_name">
                {{ t.from_shift_name }}（#{{ t.from_shift_id }}，{{ t.from_keeper }}）
              </span>
              <span v-else style="color:var(--muted)">—</span>
            </td>
            <td>
              <span class="tag on">
                {{ t.to_shift_name }}（#{{ t.to_shift_id }}，{{ t.to_keeper }}）
              </span>
            </td>
            <td style="white-space:nowrap">{{ t.switched_at }}</td>
            <td>#{{ t.handover_id }}</td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="6" style="color:var(--muted)">暂无切换证明</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
