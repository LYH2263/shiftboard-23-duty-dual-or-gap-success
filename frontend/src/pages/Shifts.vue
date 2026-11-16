<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await getJson('/api/shifts')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>班次</h1>
    <p class="lead">
      每个班次绑定可值班责任区集合；只有持有责任区值班权的班次才显示「值班中」，
      班次状态由值班权派生。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr><th>编号</th><th>班次</th><th>值班人</th><th>状态</th><th>可值班责任区</th><th>当前持权责任区</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td>{{ s.id }}</td>
            <td>{{ s.name }}</td>
            <td>{{ s.keeper }}</td>
            <td><span class="tag" :class="{ on: s.status === '值班中' }">{{ s.status }}</span></td>
            <td>
              <span v-for="z in s.zones" :key="z.id" class="tag" style="margin-right:6px">
                {{ z.name }}
              </span>
            </td>
            <td>
              <span v-for="z in s.zones.filter((x) => x.on_duty)" :key="'d'+z.id" class="tag on" style="margin-right:6px">
                {{ z.name }}
              </span>
              <span v-if="!s.duty_zone_ids.length" style="color:var(--muted)">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
