<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const shifts = ref([])
const actorId = ref(1)
const err = ref('')
const msg = ref('')
const ok = ref(false)

async function refresh() {
  rows.value = await getJson('/api/batches')
  shifts.value = await getJson('/api/shifts')
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function act(path, body) {
  msg.value = ''
  ok.value = false
  try {
    await postJson(path, body)
    ok.value = true
    msg.value = '操作成功'
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

function settle(b) {
  act(`/api/batches/${b.id}/settle`, { actor_shift_id: Number(actorId.value) })
}

function reassign(b) {
  act(`/api/batches/${b.id}/reassign`, {
    actor_shift_id: Number(actorId.value),
    new_owner_shift_id: Number(b.duty_shift_id),
  })
}
</script>

<template>
  <div>
    <h1>在途批次</h1>
    <p class="lead">
      批次归属某责任区；非值班班次禁止结清/改归属，冲突时提示当前值班班次。
      改归属只能交给该责任区当前值班班次，防止绕过值班权转移在途批次。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <div class="row">
        <label>以哪个班次身份操作
          <select v-model.number="actorId">
            <option v-for="s in shifts" :key="s.id" :value="s.id">
              {{ s.name }} · {{ s.keeper }}（{{ s.status }}）
            </option>
          </select>
        </label>
      </div>
      <p class="msg" :class="{ ok }" v-if="msg">{{ msg }}</p>
      <table>
        <thead>
          <tr>
            <th>业务键</th><th>标题</th><th>责任区</th><th>归属班次</th>
            <th>区当前值班</th><th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in rows" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.zone_name || '未指派' }}</td>
            <td>{{ b.owner_shift_name || '—' }}</td>
            <td>
              <span v-if="b.duty_shift_name" class="tag on">{{ b.duty_shift_name }}</span>
              <span v-else class="tag">无值班</span>
            </td>
            <td><span class="tag" :class="{ on: b.status === '在途' }">{{ b.status }}</span></td>
            <td>
              <button
                v-if="b.status === '在途'"
                class="btn ghost"
                style="padding:4px 10px;margin-right:6px"
                @click="settle(b)"
              >结清</button>
              <button
                v-if="b.status === '在途' && b.duty_shift_id && b.duty_shift_id !== b.owner_shift_id"
                class="btn ghost"
                style="padding:4px 10px"
                @click="reassign(b)"
              >转给当前值班</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
