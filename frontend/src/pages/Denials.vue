<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')
const operation = ref('')
const operations = ['', '结清', '改归属', '发起交班', '完成交班']

async function load() {
  err.value = ''
  try {
    const qs = operation.value ? `?operation=${encodeURIComponent(operation.value)}` : ''
    rows.value = await getJson(`/api/denials${qs}`)
  } catch (e) {
    err.value = String(e.message || e)
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1>操作留痕</h1>
    <p class="lead">
      责任区资格、单值班冲突、交班单与在途操作的失败原因统一落库（operation_denials），与业务数据同源。
    </p>
    <div class="panel">
      <div class="row">
        <label>操作类型
          <select v-model="operation" @change="load">
            <option v-for="op in operations" :key="op" :value="op">{{ op || '全部' }}</option>
          </select>
        </label>
      </div>
      <p v-if="err" class="msg">{{ err }}</p>
      <table>
        <thead>
          <tr><th>时间</th><th>操作</th><th>操作班次</th><th>责任区</th><th>批次</th><th>交班单</th><th>失败原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="d in rows" :key="d.id">
            <td style="white-space:nowrap">{{ d.created_at }}</td>
            <td>{{ d.operation }}</td>
            <td>{{ d.actor_shift_name || '—' }}</td>
            <td>{{ d.zone_name || '—' }}</td>
            <td>{{ d.batch_biz_key || '—' }}</td>
            <td>{{ d.handover_id ? '#' + d.handover_id : '—' }}</td>
            <td style="color:var(--danger)">{{ d.reason }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="7" style="color:var(--muted)">暂无记录</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
