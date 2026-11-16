<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const zones = ref([])
const err = ref('')

onMounted(async () => {
  try {
    zones.value = await getJson('/api/zones')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>责任区与值班权</h1>
    <p class="lead">
      每个责任区同一时刻至多一个值班班次（数据库唯一约束保证）；
      下表同时给出当前值班班次与可值班班次集合。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel" v-for="z in zones" :key="z.id">
      <h2>{{ z.name }} <span class="tag">{{ z.code }}</span></h2>
      <table>
        <tbody>
          <tr>
            <th style="width:160px">当前值班班次</th>
            <td>
              <span v-if="z.duty_shift_id" class="tag on">
                {{ z.duty_shift_name }}（#{{ z.duty_shift_id }}，值班人 {{ z.duty_keeper }}）
              </span>
              <span v-else class="tag">无值班（空窗）</span>
              <span v-if="z.duty_since" style="margin-left:10px;color:var(--muted);font-size:12px">
                自 {{ z.duty_since }}
              </span>
            </td>
          </tr>
          <tr>
            <th>可值班班次集合</th>
            <td>
              <span v-for="s in z.qualified_shifts" :key="s.id" class="tag" style="margin-right:6px">
                {{ s.name }} · {{ s.keeper }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
