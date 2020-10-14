from hugectr import Session, solver_parser_helper, LrPolicy_t, MetricsType

def session_impl_test():
  json_name = "./criteo_data/criteo_bin.json"
  solver_config = solver_parser_helper(batchsize = 512, batchsize_eval = 512, lr_policy = LrPolicy_t.fixed, vvgpu = [[0]], i64_input_key = False, metrics_spec = {MetricsType.AUC: 0.8025, MetricsType.AverageLoss: 0.005})
  sess = Session(solver_config, json_name)
  for i in range(10000):
    sess.train()
    if (i%100 == 0):
      loss = sess.get_current_loss()
      print("iter: {}; loss: {}".format(i, loss))
    if (i%1000 == 0 and i != 0):
      metrics = sess.evaluation()
      print(metrics)

def learning_rate_scheduler_test(config_file):
  lr_sch = get_learning_rate_scheduler(config_file)
  for i in range(10000):
    lr = lr_sch.get_next()
    print("iter: {}, lr: {}".format(i, lr))

if __name__ == "__main__":
  session_impl_test()
