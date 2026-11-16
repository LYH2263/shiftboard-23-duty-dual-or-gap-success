<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await getJson('/api/handovers')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>交班记录</h1>
    <p class="lead">
      每单交班留档：双方班次、清单批次数、最终状态，以及同事务落库的值班切换证明
      （区、旧值班 → 新值班、切换时刻），与交班单同源可查。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel" v-for="h in rows" :key="h.id">
      <h2>
        交班单 #{{ h.id }}
        <span class="tag" :class="{ on: h.status === '已完成' }">{{ h.status }}</span>
      </h2>
      <table>
        <tbody>
          <tr>
            <th style="width:160px">交班方 → 接班方</th>
            <td>{{ h.from_shift_name }} → {{ h.to_shift_name }}</td>
          </tr>
          <tr>
            <th>责任区 / 批次数</th>
            <td>
              {{ (h.zones || []).map((z) => z.name).join('、') || '—' }}
              · {{ h.batch_count }} 批
            </td>
          </tr>
          <tr>
            <th>开始 / 结束</th>
            <td>{{ h.started_at }} / {{ h.finished_at || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin:12px 0 6px;font-size:14px">值班切换证明</h3>
      <table v-if="h.transitions && h.transitions.length">
        <thead>
          <tr><th>证明</th><th>责任区</th><th>旧值班</th><th>新值班</th><th>切换时刻</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in h.transitions" :key="t.id">
            <td>#{{ t.id }}</td>
            <td>{{ t.zone_name }}</td>
            <td>{{ t.from_shift_name || '—' }}</td>
            <td><span class="tag on">{{ t.to_shift_name }}</span></td>
            <td style="white-space:nowrap">{{ t.switched_at }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else style="color:var(--muted);font-size:12px">
        本单未完成值班切换（无证明，值班权未改动）
      </p>
    </div>
  </div>
</template>
