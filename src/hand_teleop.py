import serial
from serial.tools import list_ports
import struct
import platform
import cv2
import mediapipe as mp
import csv
import os

OBJECT = 'horse'
DATA_DIR = '../data'
POS_CSV = os.path.join(DATA_DIR, f'{OBJECT}_pos.csv')
TOUCH_CSV = os.path.join(DATA_DIR, f'{OBJECT}_touch.csv')

mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles
mp_hands = mp.solutions.hands

# search for serial port to use
def initSerial():
  print('Searching for serial ports...')
  com_ports_list = list(list_ports.comports())
  port = ''

  for p in com_ports_list:
    if(p):
      if platform.system() == 'Linux' or platform.system() == 'Darwin':
        if 'USB' in p[0]:
          port = p
          print('Found:', port)
          break
      elif platform.system() == 'Windows':
        if 'COM' in p[0]:
          port = p
          print('Found:', port)
          break
  if not port:
    print('No port found')
    quit()
    
  try: 
    print('Connecting...')
    ser = serial.Serial(port[0], 460800, timeout = 0.02)
    print('Connected!')
  except: 
    print('Failed to Connect!')
    exit()

  ser.reset_input_buffer()
    
  return ser

# generate msg to send to hand from pos array
def generateTX(positions):
  txBuf = []
  txBuf.append((struct.pack('<B', 0x50))[0])
  txBuf.append((struct.pack('<B', 0x10))[0])
  for i in range(0,6):
    posFixed = int(positions[i] * 32767 / 150)
    txBuf.append((struct.pack('<B',(posFixed & 0xFF)))[0])
    txBuf.append((struct.pack('<B',(posFixed >> 8) & 0xFF))[0])
  cksum = 0
  for b in txBuf:
    cksum = cksum + b
  cksum = (-cksum) & 0xFF
  txBuf.append((struct.pack('<B', cksum))[0])
  return txBuf

def extractData(ser):
  pos_data = [0.0] * 6
  touch_data = [0.0] * 30

  data = ser.read(1)
  if len(data) != 1:
    return pos_data, touch_data

  replyFormat = data[0]
  if (replyFormat & 0xF) == 2:
    replyLen = 38
  else:
    replyLen = 71
  data = ser.read(replyLen)

  if len(data) == replyLen:

    # extract pos data
    for i in range(6):
      rawData = struct.unpack('<h', data[i*4:2+(i*4)])[0]
      pos_data[i] = rawData * 150 / 32767
    pos_data[5] = -pos_data[5]

    # extract touch data
    if replyLen == 71:
      for i in range(15):
        dualData = data[(i*3)+24:((i+1)*3)+24]
        data1 = struct.unpack('<H', dualData[0:2])[0] & 0x0FFF
        data2 = (struct.unpack('<H', dualData[1:3])[0] & 0xFFF0) >> 4
        touch_data[i*2] = int(data1)
        touch_data[(i*2)+1] = int(data2)

  return pos_data, touch_data

def getPose(frame):
  # run hand tracker
  frame.flags.writeable = False
  frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
  results = hands.process(frame)
  frame.flags.writeable = True
  frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

  # extract hand pose
  pose = None
  if results.multi_hand_landmarks:
    for hand, hand_landmarks in zip(results.multi_handedness, results.multi_hand_landmarks):
      if hand.classification[0].label == 'Left':
        pose = hand_landmarks.landmark
        mp_drawing.draw_landmarks(
          frame,
          hand_landmarks,
          mp_hands.HAND_CONNECTIONS,
          mp_drawing_styles.get_default_hand_landmarks_style(),
          mp_drawing_styles.get_default_hand_connections_style())

  # flip frame for selfie-view
  return pose, cv2.flip(frame, 1)

def getMsg(pose):
  msg = [15.0] * 6

  # scalar for standardizing hand size
  z = 4.0 / (dist(pose[0], pose[5]) + dist(pose[0], pose[9])
                  + dist(pose[0], pose[13]) + dist(pose[0], pose[17]))

  msg[0] = interpolate(z * dist(pose[0], pose[8]), 0.9, 1.9) # index finger
  msg[1] = interpolate(z * dist(pose[0], pose[12]), 0.8, 2.0) # middle finger
  msg[2] = interpolate(z * dist(pose[0], pose[16]), 0.7, 1.9) # ring finger
  msg[3] = interpolate(z * dist(pose[0], pose[20]), 0.7, 1.8) # pinky finger
  msg[4] = interpolate(z * dist(pose[0], pose[4]), 0.9, 1.2) # thumb
  msg[5] = -interpolate(z * dist(pose[2], pose[17]), 0.6, 0.9) # thumb rotator

  return msg

def interpolate(dist, real_min, real_max):
  JOINT_MIN = 0
  JOINT_MAX = 100
  p = (dist - real_min) / (real_max - real_min)
  control = p * JOINT_MIN + (1 - p) * JOINT_MAX
  return min(max(control, JOINT_MIN), JOINT_MAX)

def dist(landmark_1, landmark_2):
  dx = landmark_2.x - landmark_1.x
  dy = landmark_2.y - landmark_1.y
  return (dx * dx + dy * dy)**0.5

if __name__ == '__main__':
  ser = initSerial()
  cap = cv2.VideoCapture(0)
  msg = [15.0] * 6

  if not os.path.isdir(DATA_DIR):
    os.makedirs(DATA_DIR)

  if os.path.isfile(POS_CSV):
    with open(POS_CSV, 'r') as pos_csv:
      i = sum(1 for row in pos_csv)
  else:
    i = 0

  with open(POS_CSV, 'a') as pos_csv, open(TOUCH_CSV, 'a') as touch_csv:
    pos_writer = csv.writer(pos_csv)
    touch_writer = csv.writer(touch_csv)

    with mp_hands.Hands(
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5) as hands:

      while cap.isOpened():
        success, frame = cap.read()
        if not success:
          print('empty camera frame')
          continue

        # get right hand pose from mediapipe
        pose, frame = getPose(frame)

        # get message from pose
        if pose:
          msg = getMsg(pose)

        # send message to psyonic hand
        ser.write(generateTX(msg))

        # read first response byte
        pos_data, touch_data = extractData(ser)

        cv2.imshow('MediaPipe', frame)
        key = cv2.waitKey(1)
        if key & 0xFF == ord(' '):
          if all(pos == 0 for pos in pos_data) and all(touch == 0 for touch in touch_data):
            print()
            print('null sample')
            continue
          pos_writer.writerow(pos_data)
          touch_writer.writerow(touch_data)
          print()
          print(f'sample {i + 1}:')
          print(pos_data)
          print(touch_data)
          i += 1
        elif key & 0xFF == ord('q'):
          break

  cap.release()
  ser.close()		
