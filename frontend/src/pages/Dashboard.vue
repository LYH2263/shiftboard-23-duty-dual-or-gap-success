<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const data = ref(null)
const err = ref('')

onMounted(async () => {
  try {
    data.value = await getJson('/api/dashboard')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>总览</h1>
    <p class="lead">看清每个责任区谁在值班、有多少在途批次、有没有交班卡在半路。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <template v-if="data">
      <div class="stats">
        <div class="stat"><div class="k">班次数</div><div class="v">{{ data.班次数 }}</div></div>
        <div class="stat"><div class="k">责任区数</div><div class="v">{{ data.责任区数 }}</div></div>
        <div class="stat"><div class="k">在途批次</div><div class="v">{{ data.在途批次数 }}</div></div>
        <div class="stat"><div class="k">进行中交班</div><div class="v">{{ data.进行中交班 }}</div></div>
      </div>
      <div class="panel">
        <h2>责任区值班态</h2>
        <table>
          <thead><tr><th>责任区</th><th>当前值班班次</th><th>值班人</th><th>持权起始</th></tr></thead>
          <tbody>
            <tr v-for="z in data.责任区值班" :key="z.id">
              <td>{{ z.name }}</td>
              <td>
                <span v-if="z.shift_id" class="tag on">{{ z.shift_name }}</span>
                <span v-else class="tag">无值班（空窗）</span>
              </td>
              <td>{{ z.keeper || '—' }}</td>
              <td>{{ z.since || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="panel">
        <h2>班次状态</h2>
        <table>
          <thead><tr><th>班次</th><th>值班人</th><th>状态</th><th>持有责任区</th></tr></thead>
          <tbody>
            <tr v-for="s in data.班次" :key="s.id">
              <td>{{ s.name }}</td>
              <td>{{ s.keeper }}</td>
              <td><span class="tag" :class="{ on: s.status === '值班中' }">{{ s.status }}</span></td>
              <td style="color:var(--muted)">{{ s.status === '值班中' ? '见责任区值班态' : '无' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
