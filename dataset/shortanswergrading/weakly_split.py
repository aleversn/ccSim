# %%
with open('./dataset/shortanswergrading/train_zh.csv', encoding='utf-8') as f:
    ori_list = f.read().split('\n')

weakly_1 = ''
weakly_2 = ''
for line in ori_list:
    if line.strip() == '':
        continue
    if int(line.split('\t')[0]) <= 10:
        weakly_1 += line + '\n'
    else:
        weakly_2 += line + '\n'

with open('./dataset/shortanswergrading/s_w1_zh.csv', encoding='utf-8', mode='w+') as f:
    f.write(weakly_1)

with open('./dataset/shortanswergrading/s_w2_zh.csv', encoding='utf-8', mode='w+') as f:
    f.write(weakly_2)

# %%
